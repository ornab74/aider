from __future__ import annotations
from collections import defaultdict, deque
from dataclasses import dataclass
from enum import Enum

class PlanStepKind(str, Enum):
    SEARCH='search'; READ='read'; EDIT='edit'; TEST='test'; VERIFY='verify'; APPROVE='approve'; PUBLISH='publish'

@dataclass(frozen=True)
class PlanStep:
    identifier:str; kind:PlanStepKind; description:str; depends_on:tuple[str,...]=(); inputs:tuple[str,...]=(); outputs:tuple[str,...]=(); risk_score:int=0; requires_approval:bool=False; idempotency_key:str=''

@dataclass(frozen=True)
class ExecutionPlan:
    task:str; steps:tuple[PlanStep,...]; max_parallel:int=1

@dataclass(frozen=True)
class PlanIssue:
    code:str; step_id:str|None; message:str; blocking:bool=True

@dataclass(frozen=True)
class PlanValidation:
    valid:bool; issues:tuple[PlanIssue,...]; topological_order:tuple[str,...]

class ExecutionPlanValidator:
    def validate(self, plan:ExecutionPlan)->PlanValidation:
        issues=[]; by={}
        for step in plan.steps:
            if step.identifier in by: issues.append(PlanIssue('duplicate-step',step.identifier,'duplicate step'))
            by[step.identifier]=step
        for step in plan.steps:
            for dep in step.depends_on:
                if dep not in by: issues.append(PlanIssue('missing-dependency',step.identifier,f'missing {dep}'))
            if step.risk_score>=45 and step.kind in {PlanStepKind.EDIT,PlanStepKind.APPROVE,PlanStepKind.PUBLISH} and not step.requires_approval:
                issues.append(PlanIssue('approval-required',step.identifier,'high-risk step requires approval'))
            if step.kind==PlanStepKind.PUBLISH:
                ancestors=self._ancestors(step.identifier,by); kinds={by[x].kind for x in ancestors if x in by}
                if PlanStepKind.TEST not in kinds: issues.append(PlanIssue('publish-without-tests',step.identifier,'publish requires tests'))
                if PlanStepKind.VERIFY not in kinds: issues.append(PlanIssue('publish-without-verification',step.identifier,'publish requires verification'))
                if step.risk_score>=45 and PlanStepKind.APPROVE not in kinds: issues.append(PlanIssue('publish-without-approval',step.identifier,'publish requires approval'))
        owners=defaultdict(list)
        for step in plan.steps:
            for out in step.outputs: owners[out].append(step)
        for out,writers in owners.items():
            if len(writers)>1 and not all(a.identifier in b.depends_on or b.identifier in a.depends_on for i,a in enumerate(writers) for b in writers[i+1:]):
                issues.append(PlanIssue('parallel-output-conflict',None,f'unordered writers for {out}'))
        keys=defaultdict(list)
        for s in plan.steps:
            if s.idempotency_key: keys[s.idempotency_key].append(s.identifier)
        for key,ids in keys.items():
            if len(ids)>1: issues.append(PlanIssue('duplicate-idempotency-key',None,f'{ids} share {key}'))
        order,cyclic=self._topo(by)
        if cyclic: issues.append(PlanIssue('dependency-cycle',None,'cycle detected'))
        return PlanValidation(not any(i.blocking for i in issues),tuple(issues),order)
    def _ancestors(self,ident,by):
        out=set(); pending=list(by[ident].depends_on)
        while pending:
            x=pending.pop()
            if x in out: continue
            out.add(x)
            if x in by: pending.extend(by[x].depends_on)
        return out
    def _topo(self,by):
        indeg={k:0 for k in by}; children=defaultdict(list)
        for s in by.values():
            for d in s.depends_on:
                if d in by: indeg[s.identifier]+=1; children[d].append(s.identifier)
        q=deque(sorted(k for k,v in indeg.items() if v==0)); out=[]
        while q:
            x=q.popleft(); out.append(x)
            for c in sorted(children[x]):
                indeg[c]-=1
                if indeg[c]==0:q.append(c)
        return tuple(out),len(out)!=len(by)
