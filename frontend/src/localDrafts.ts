import { ref } from 'vue'
import type { BusinessOperation, Draft } from './useSharedPlan'
export interface PendingCommand {base_revision:number;input_version:number;latest_adoption:number;operations:BusinessOperation[];operation_id:string;pending:boolean}
export interface Outstanding {command:PendingCommand;kind:'save'|'adopt';remaining_operations?:BusinessOperation[]}
export interface LocalRecord {data_version:2;draft_id:string;updated_at:string;draft:Draft;pending:boolean;outstanding:Outstanding|null}
export interface RecoveryRecord {id:string;key:string;raw:string;record:LocalRecord|null;problem:string}
const retention=30*24*60*60*1000
const isObject=(value:unknown):value is Record<string,unknown>=>typeof value==='object' && value!==null && !Array.isArray(value)
const version=(value:unknown)=>Number.isSafeInteger(value) && Number(value)>=0
function operations(value:unknown):BusinessOperation[] {
  if(!Array.isArray(value))throw new Error('动作列表无法转换')
  return value.map(row=>{
    if(!isObject(row) || typeof row.kind!=='string' || row.kind==='replace_plan' || 'plan' in row || 'evaluation' in row)throw new Error('记录包含整份计划或无法转换的动作')
    if(row.action_id!==undefined && typeof row.action_id!=='string')throw new Error('动作编号无法转换')
    return {...row,action_id:row.action_id ?? crypto.randomUUID()} as BusinessOperation
  })
}
export function validatedDraft(value:unknown):Draft {
  if(!isObject(value) || value.data_version!==1 || !version(value.base_revision) || !version(value.input_version) || !version(value.latest_adoption))throw new Error('数据版本无法转换，原始JSON已保留')
  const actions=operations(value.operations)
  if(value.selected_actions!==undefined && (!Array.isArray(value.selected_actions) || value.selected_actions.some(id=>typeof id!=='string' || !actions.some(action=>action.action_id===id))))throw new Error('动作选择无法转换')
  return {data_version:1,base_revision:Number(value.base_revision),input_version:Number(value.input_version),latest_adoption:Number(value.latest_adoption),operations:actions,selected_actions:(value.selected_actions as string[]|undefined) ?? actions.map(action=>action.action_id!)}
}
function outstanding(value:unknown):Outstanding|null {
  if(value===null || value===undefined)return null
  if(!isObject(value) || !isObject(value.command) || !['save','adopt'].includes(String(value.kind)) || typeof value.command.operation_id!=='string' || typeof value.command.pending!=='boolean')throw new Error('前次保存编号无法转换，须保留原始JSON并核对共享计划')
  const draft=validatedDraft({...value.command,data_version:1})
  const {data_version:_,selected_actions:__,...tokens}=draft
  return {kind:value.kind as 'save'|'adopt',command:{...tokens,operation_id:value.command.operation_id,pending:value.command.pending},remaining_operations:value.remaining_operations===undefined ? [] : operations(value.remaining_operations)}
}
export function draftStorage(prefix:string,account:string) {
  const path=('/'+prefix.split('/').filter(Boolean).join('/'))
  const normalized=account.trim().toUpperCase()
  const namespace=`auto-plan:${encodeURIComponent(path)}:${encodeURIComponent(normalized)}:draft:`
  const key=(id:string)=>namespace+encodeURIComponent(id)
  function list():RecoveryRecord[] {
    const found:RecoveryRecord[]=[]
    for(let i=0;i<localStorage.length;i++){
      const storageKey=localStorage.key(i)!
      if(!storageKey.startsWith(namespace))continue
      const id=decodeURIComponent(storageKey.slice(namespace.length)),raw=localStorage.getItem(storageKey)!
      try {
        const value=JSON.parse(raw)
        let record:LocalRecord
        if(isObject(value) && value.data_version===2){
          if(value.draft_id!==id || typeof value.updated_at!=='string' || !Number.isFinite(Date.parse(value.updated_at)) || typeof value.pending!=='boolean')throw new Error('本机记录元数据无法转换')
          record={data_version:2,draft_id:id,updated_at:value.updated_at,draft:validatedDraft(value.draft),pending:value.pending,outstanding:outstanding(value.outstanding)}
        }else{
          const draft=validatedDraft(value)
          record={data_version:2,draft_id:id,updated_at:new Date().toISOString(),draft,pending:false,outstanding:null}
        }
        if(Date.now()-Date.parse(record.updated_at)>retention && !record.outstanding){localStorage.removeItem(storageKey);i--;continue}
        found.push({id,key:storageKey,raw,record,problem:''})
      }catch(e){found.push({id,key:storageKey,raw,record:null,problem:e instanceof Error ? e.message : '数据无法转换'})}
    }
    // Migrate #29's operation-only unknown record without discarding its original key.
    const legacyKey=`${prefix}:${normalized}:shared-plan:unknown`,legacy=localStorage.getItem(legacyKey)
    if(legacy && !found.some(row=>row.id==='main')){
      try{
        const value=outstanding(JSON.parse(legacy))!
        const draft=validatedDraft({...value.command,data_version:1,operations:[...value.command.operations,...(value.remaining_operations ?? [])],selected_actions:value.command.operations.map(action=>action.action_id!)})
        found.push({id:'main',key:legacyKey,raw:legacy,problem:'',record:{data_version:2,draft_id:'main',updated_at:new Date().toISOString(),draft,pending:value.command.pending,outstanding:value}})
      }catch(e){found.push({id:'main',key:legacyKey,raw:legacy,record:null,problem:e instanceof Error ? e.message : '旧保存记录无法转换'})}
    }
    return found.sort((a,b)=>a.id.localeCompare(b.id))
  }
  return {namespace,key,list,write:(record:LocalRecord)=>localStorage.setItem(key(record.draft_id),JSON.stringify(record)),remove:(record:RecoveryRecord)=>localStorage.removeItem(record.key)}
}

export function draftLease(name:string) {
  const owned=ref(false),supported=!!navigator.locks,issue=ref('')
  const owner=crypto.randomUUID(),channel=typeof BroadcastChannel==='function' ? new BroadcastChannel(name) : null
  let release:(()=>void)|undefined,closed=false,generation=0
  function relinquish(){owned.value=false;release?.();release=undefined}
  if(channel)channel.onmessage=event=>{if(event.data?.type==='owner' && event.data.owner!==owner)relinquish()}
  async function acquire(steal=false){
    if(!supported){issue.value='当前浏览器不支持 Web Locks，请使用 Windows Chrome 后编辑';return}
    const attempt=++generation
    try{
      await navigator.locks.request(name,steal ? {steal:true} : {ifAvailable:true},async lock=>{
        if(!lock || closed || attempt!==generation)return
        owned.value=true;issue.value='';channel?.postMessage({type:'owner',owner})
        await new Promise<void>(resolve=>{release=resolve})
        if(attempt===generation)owned.value=false
      })
    }catch{if(attempt===generation){owned.value=false;issue.value='本机临时稿已由其他标签页接管'}}
  }
  void acquire()
  return {owned,supported,issue,takeOver:()=>{relinquish();void acquire(true)},close:()=>{closed=true;generation++;relinquish();channel?.close()}}
}
