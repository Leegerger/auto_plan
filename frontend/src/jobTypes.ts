import type { BusinessOperation, Plan } from './useSharedPlan'
import type { OutputEvaluation } from './outputTypes'
import type { InputSnapshot } from './inputFields'
import type { ExecutionContext, ExecutionCoverage, ResetSnapshot } from './executionTypes'
import type { LockedCell, PlanningPreferences } from './stabilityTypes'
import type { LineStateContext } from './lineStateTypes'
import type { SwitchContinuityContext } from './switchContinuityTypes'

export interface ComputationSource {
  source_revision:number;input_version:number;latest_adoption:number
  snapshot:{time:{as_of:string;window_start:string;weeks:number};inputs:InputSnapshot;plan:Plan;
    execution:ExecutionContext;reset:ResetSnapshot|null;locks:LockedCell[];preferences:PlanningPreferences;
    line_states?:LineStateContext;state_reviews?:{id:string;line_code:string;review_after:string;reason:string}[];switch_continuity?:SwitchContinuityContext;
    baseline:{version:number;plan:Plan;coverage:ExecutionCoverage[]}|null}
}

export interface SolveRequest {
  scope:{start_date:string;end_date:string;line_codes:string[];model_codes:string[]}
  mode:'local'|'new_week'|'all_replan'
  strategy:'hybrid'|'cp_sat'|'highs'
  time_limit_seconds:number
  max_candidates:number
  native_threads:number
}
export interface JobCandidateRef {candidate_id:string;payload_sha:string;expired:boolean}
export interface StageBound {stage:string;neighborhood:string;status:string;objective:string[]|null;lower_bound:string[]|null;global_proof:boolean;detail:string;route?:string|null}
export interface JobResult {status?:string;solve_status?:string;elapsed_seconds?:number;global_optimal?:boolean;stage_bounds?:StageBound[];issues?:Record<string,unknown>[];reason?:string}
export interface Job {
  job_id:string;actor:string;fifo:number;source_revision:number;input_version:number;latest_adoption:number
  state:'queued'|'running'|'completed'|'stopped'|'interrupted'|'failed'
  stop_requested_at:string|null;stop_reason:string|null;progress:Record<string,unknown>|null;result:JobResult|null
  created_at:string;started_at:string|null;ended_at:string|null;candidates:JobCandidateRef[]
}
export interface JobList {jobs:Job[];queue_capacity:number;worker_limit:number}
export type SubmissionResult={status:'ACCEPTED';submission_id:string;job_id:string}|{status:'ABORTED_VOID';submission_id:string}
export interface CandidateView {
  job_id:string;candidate_id:string;payload_sha:string
  validity:{state:'requires_recheck'|'view_only';current_input_version:number}
  candidate:{id:string;source_revision:number;input_version:number;latest_adoption:number;snapshot?:ComputationSource['snapshot'];plan:Plan;operation:Record<string,unknown>;evaluation:OutputEvaluation;objective:string[];stage_bounds:StageBound[];eligibility:'executable'|'pending_confirmation';tradeoff:string}
  proposal:{base_revision:number;input_version:number;latest_adoption:number;operations:BusinessOperation[]}
}
