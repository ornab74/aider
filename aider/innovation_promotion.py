from __future__ import annotations
from dataclasses import dataclass
from enum import Enum
class PromotionStage(str,Enum): DRAFT='draft'; CANDIDATE='candidate'; VERIFIED='verified'; MERGE_READY='merge-ready'; BLOCKED='blocked'
@dataclass(frozen=True)
class PromotionEvidence:
    plan_valid:bool; snapshot_clean:bool; contracts_valid:bool; tests_passed:bool; mutation_coverage:float; replay_valid:bool; quorum_approvals:int; required_quorum:int; risk_score:int; pressure_band:str='low'; disagreement_consensus:str='approve'; unresolved_findings:int=0
@dataclass(frozen=True)
class PromotionDecision:
    stage:PromotionStage; allowed:bool; blockers:tuple[str,...]; warnings:tuple[str,...]
class ChangePromotionPolicy:
    def __init__(self,normal_mutation_threshold=.5,high_risk_mutation_threshold=.75): self.normal=normal_mutation_threshold; self.high=high_risk_mutation_threshold
    def evaluate(self,e):
        blockers=[]; warnings=[]
        if not e.plan_valid:blockers.append('execution plan is invalid')
        if not e.snapshot_clean:blockers.append('repository drifted from the reviewed snapshot')
        if blockers:return PromotionDecision(PromotionStage.BLOCKED,False,tuple(blockers),())
        if not e.contracts_valid:blockers.append('blocking invariants are violated')
        if not e.tests_passed:blockers.append('selected tests have not passed')
        threshold=self.high if e.risk_score>=45 else self.normal
        if e.mutation_coverage<threshold:blockers.append('mutation coverage is below threshold')
        if blockers:return PromotionDecision(PromotionStage.CANDIDATE,False,tuple(blockers),())
        if not e.replay_valid:blockers.append('deterministic replay proof is invalid')
        if e.quorum_approvals<e.required_quorum:blockers.append('independent approval quorum is incomplete')
        if e.disagreement_consensus not in {'approve','no-verdicts'}:blockers.append('verifier disagreement remains unresolved')
        if e.unresolved_findings:blockers.append('verifier findings remain unresolved')
        if e.pressure_band=='critical':blockers.append('context pressure was critical during verification')
        elif e.pressure_band=='high':warnings.append('verification ran under high context pressure')
        if blockers:return PromotionDecision(PromotionStage.VERIFIED,False,tuple(blockers),tuple(warnings))
        return PromotionDecision(PromotionStage.MERGE_READY,True,(),tuple(warnings))
