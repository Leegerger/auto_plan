export type CellValue = string | number | boolean | null
export interface InputRow { [field: string]: CellValue }
export interface InputSnapshot { schema_version: number; input_version: number; masters: Record<string, InputRow[]>; parameters: Record<string, InputRow[]>; calendar: Record<string, InputRow[]>; business: Record<string, InputRow[]> }
export interface InputError { sheet: string; row: number; field: string; reason: string }
export interface InputPreview { valid: boolean; base_version: number; errors: InputError[]; changes: Record<string, InputRow[]>; snapshot: InputSnapshot | null }
export interface InputCategory { kind: string; title: string; section: 'masters' | 'parameters' | 'calendar' | 'business'; fields: string[]; keys: string[]; note: string }
export const categories: InputCategory[] = [
  {kind:'confirmations',title:'跨周确认批次与禁令',section:'business',fields:['model_code','week','kind','batch_id','status','date','quantity','note'],keys:['model_code','week','kind','batch_id'],note:'物料 material 的日期为最早投入日、数量为投入片；出货 shipping 的日期为最晚产出日、数量为合格产出片。allowed允许、pending待确认、forbidden整周跨周禁令、revoked撤销。允许批次必填日期和数量；待确认、禁令或撤销可留空。每批是新增额度；相同批号修改替换原额度。解除禁令需明确将该批修订为revoked；其他禁令仍有效。'},
  {kind:'recipes',title:'Recipe',section:'masters',fields:['code','name','active'],keys:['code'],note:'编码固定。停用后保留历史，并退出新增选择。'},
  {kind:'models',title:'机种',section:'masters',fields:['code','name','active','recipe_code'],keys:['code','recipe_code'],note:'每个机种固定一个 Recipe，创建后不能更换。'},
  {kind:'lines',title:'线体',section:'masters',fields:['code','name','active'],keys:['code'],note:'编码固定。名称可改，停用后保留引用历史。'},
  {kind:'capabilities',title:'可生产能力',section:'masters',fields:['recipe_code','line_code','allowed'],keys:['recipe_code','line_code'],note:'明确不能生产与缺产能、零产能分别记录。'},
  {kind:'line_yields',title:'统一线体良率',section:'parameters',fields:['month','percent'],keys:['month'],note:'全厂统一。百分数填 95.50 表示 95.50%，范围大于 0 至 100，最多两位小数。'},
  {kind:'model_yields',title:'机种良率',section:'parameters',fields:['month','percent','model_code'],keys:['month','model_code'],note:'按机种和月份维护。填 98 表示 98%，最多两位小数。'},
  {kind:'capacities',title:'全日产能',section:'parameters',fields:['month','recipe_code','line_code','pieces'],keys:['month','recipe_code','line_code'],note:'连续 24 小时可投入的非负整数片数。显式零与缺失不同。'},
  {kind:'cycle_times',title:'Cycle Time',section:'parameters',fields:['model_code','days'],keys:['model_code'],note:'以天为单位，非负，最多两位小数，零允许。'},
  {kind:'switch_durations',title:'有向切机时间',section:'parameters',fields:['from_recipe','to_recipe','hours'],keys:['from_recipe','to_recipe'],note:'以小时为单位，非负，最多两位小数。缺少已知不同 Recipe 的参数时使用 12 小时，并标记缺失。'},
  {kind:'holidays',title:'全厂 Holiday',section:'calendar',fields:['date','holiday'],keys:['date'],note:'按北京时间生产日登记，08:00 至次日 08:00。取消 Holiday 请设为否。'},
  {kind:'opening_exceptions',title:'线体开工例外',section:'calendar',fields:['date','line_code','enabled'],keys:['date','line_code'],note:'只恢复该线体 Holiday 的投入能力，不改变全厂产出日历。'},
  {kind:'maintenance',title:'保养时间',section:'calendar',fields:['date','line_code','hours'],keys:['date','line_code'],note:'非负整数小时。跨生产日请拆分，每日零头向上取整后录入。保养与实验合计超过 24 小时仍可保存。'},
  {kind:'experiments',title:'实验时间',section:'calendar',fields:['date','line_code','hours'],keys:['date','line_code'],note:'非负整数小时。与保养直接相加，合计超过 24 小时仅表示无可用工时。'},
]
export const fieldNames: Record<string, string> = {week:'需求周周一',kind:'确认类型',batch_id:'批次编号',status:'确认状态',quantity:'额度 (片)',note:'备注',code:'编码',name:'名称',active:'启用',recipe_code:'Recipe 编码',line_code:'线体编码',allowed:'允许生产',month:'月份',percent:'良率 (%)',model_code:'机种编码',pieces:'产能 (片/24小时)',days:'Cycle Time (天)',from_recipe:'原 Recipe',to_recipe:'目标 Recipe',hours:'小时',date:'生产日期',holiday:'Holiday',enabled:'例外开工',headers:'列名',columns:'列数',sheet:'工作表',file:'工作簿',table:'表格'}
