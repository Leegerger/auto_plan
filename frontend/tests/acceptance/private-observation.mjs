/** Node-clock diagnostics and error lifetime. Work, cleanup policy and persistence belong to the caller. */
import {types} from 'node:util'

export function privateObservation(record,{clock=()=>performance.now()}={}) {
  const value={schema_version:1,actor:0,clock_domain:'Node performance.now milliseconds',phases:[],primary_error:null,cleanup_errors:[],observer_errors:[]}
  let sequence=0,lastErrorPhase=null,current=null
  const note=(phase,error)=>{
    try{value.observer_errors.push({phase,error_class:typeof error?.name==='string'?error.name:'UnknownError'})}catch{}
  }
  const errorValue=(phase,error)=>{
    try{return {phase,error_class:typeof error?.name==='string'?error.name:'UnknownError',message:String(error?.message??'Unknown failure').split('\n')[0].slice(0,300)}}
    catch(observationError){note(phase,observationError);return {phase,error_class:'unknown',message:'unconfirmed'}}
  }
  const event=(phase,state,metadata={})=>{
    try{
      const at_ms=clock();if(!Number.isFinite(at_ms))throw new TypeError('Finite Node observation clock required')
      value.phases.push({sequence:sequence++,phase,state,at_ms,...metadata})
    }catch(error){note(phase,error)}
  }
  try{record.observation=value}catch(error){note('initialize',error)}
  const step=(phase,work,metadata={})=>{
    current=phase;event(phase,'begin',metadata)
    let result
    try{result=work()}catch(error){lastErrorPhase=phase;event(phase,'error',metadata);throw error}
    if(types.isPromise(result)){
      try{Promise.prototype.then.call(result,()=>event(phase,'end',metadata),()=>{lastErrorPhase=phase;event(phase,'error',metadata)})}
      catch(error){note(phase,error)}
    }else event(phase,'returned_unobserved',metadata)
    return result
  }
  const sync=(phase,work,metadata={})=>{
    current=phase;event(phase,'begin',metadata)
    try{const result=work();event(phase,'end',metadata);return result}
    catch(error){lastErrorPhase=phase;event(phase,'error',metadata);throw error}
  }
  const primary=error=>{try{value.primary_error??=errorValue(lastErrorPhase??current,error)}catch(observationError){note('primary_error',observationError)}}
  const secondary=error=>{try{value.cleanup_errors.push(errorValue(lastErrorPhase??current,error))}catch(observationError){note('cleanup_error',observationError)}}
  return {step,sync,primary,cleanup:secondary,
    async run(work,cleanup){
      let original,failed=false
      try{return await work()}
      catch(error){original=error;failed=true;primary(error);throw error}
      finally{
        try{await cleanup()}
        catch(error){secondary(error);if(failed)throw original;throw error}
      }
    }}
}
