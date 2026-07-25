from dataclasses import replace
from pathlib import Path
import pytest
from aider.innovation_disagreement import VerifierDisagreementAnalyzer,VerifierVerdict
from aider.innovation_plan import ExecutionPlan,ExecutionPlanValidator,PlanStep,PlanStepKind
from aider.innovation_promotion import ChangePromotionPolicy,PromotionEvidence,PromotionStage
from aider.innovation_replay import DeterministicReplayRecorder,DeterministicReplayVerifier,ReplayLog
from aider.innovation_snapshot import RepositorySnapshot,RepositorySnapshotter
from aider.innovation_transaction import ShadowWorkspaceTransaction,TransactionState

def _plan(risk=20):
    steps=[PlanStep('search',PlanStepKind.SEARCH,'find'),PlanStep('edit',PlanStepKind.EDIT,'edit',('search',),risk_score=risk),PlanStep('test',PlanStepKind.TEST,'test',('edit',)),PlanStep('verify',PlanStepKind.VERIFY,'verify',('test',))]
    dep=('verify',)
    if risk>=45:
        steps.append(PlanStep('approve',PlanStepKind.APPROVE,'approve',('verify',),risk_score=risk,requires_approval=True)); dep=('approve',)
    steps.append(PlanStep('publish',PlanStepKind.PUBLISH,'publish',dep,risk_score=risk,requires_approval=risk>=45))
    return ExecutionPlan('repair',tuple(steps))

def test_plan_valid():
    result=ExecutionPlanValidator().validate(_plan())
    assert result.valid and result.topological_order[-1]=='publish'

def test_plan_rejects_cycle():
    plan=ExecutionPlan('bad',(PlanStep('edit',PlanStepKind.EDIT,'e',('publish',),risk_score=80),PlanStep('publish',PlanStepKind.PUBLISH,'p',('edit',),risk_score=80)))
    codes={item.code for item in ExecutionPlanValidator().validate(plan).issues}
    assert {'dependency-cycle','approval-required','publish-without-tests'}<=codes

def test_snapshot_roundtrip(tmp_path:Path):
    snapper=RepositorySnapshotter(); before=snapper.capture({'a.py':'x=1\n','b.py':'y=2\n'}); after=snapper.capture({'a.py':'x=3\n','c.py':'z=4\n'}); delta=snapper.compare(before,after)
    assert delta.added==('c.py',) and delta.removed==('b.py',) and delta.modified==('a.py',)
    path=tmp_path/'snapshot.json'; before.save(path)
    assert RepositorySnapshot.load(path).aggregate_digest==before.aggregate_digest

def test_replay_tamper(tmp_path:Path):
    snapper=RepositorySnapshotter(); before=snapper.capture({'a.py':'x=1\n'}); after=snapper.capture({'a.py':'x=2\n'}); recorder=DeterministicReplayRecorder('x',before); event=recorder.record('edit',{'path':'a.py'},before,after); log=recorder.build()
    assert DeterministicReplayVerifier().verify(log).valid
    path=tmp_path/'replay.json'; log.save(path); assert ReplayLog.load(path)==log
    tampered=ReplayLog(log.task,log.baseline_digest,(replace(event,after_digest='bad'),))
    assert not DeterministicReplayVerifier().verify(tampered).valid

def test_disagreement_detects_patch_mismatch():
    report=VerifierDisagreementAnalyzer().analyze((VerifierVerdict('a','p1',True,.9),VerifierVerdict('b','p2',False,.8)))
    assert report.patch_mismatch and report.consensus=='patch-mismatch'

def test_transaction_commit(tmp_path:Path):
    tx=ShadowWorkspaceTransaction({'a.py':'x=1\n','old.py':'old\n'}); tx.stage('a.py','x=2\n'); tx.stage('new.py','new\n'); tx.delete('old.py'); preview=tx.seal(); assert preview.changed_paths==('a.py','new.py'); tx.commit(tmp_path,allow_write=True)
    assert tx.state==TransactionState.COMMITTED and (tmp_path/'a.py').read_text()=='x=2\n'

def test_transaction_rejects_escape():
    tx=ShadowWorkspaceTransaction({'a.py':'x=1\n'})
    with pytest.raises(ValueError): tx.stage('../x.py','bad')

def test_promotion_requires_complete_evidence():
    policy=ChangePromotionPolicy(); blocked=policy.evaluate(PromotionEvidence(True,True,True,False,.1,False,0,2,60)); assert blocked.stage==PromotionStage.CANDIDATE
    ready=policy.evaluate(PromotionEvidence(True,True,True,True,.9,True,2,2,60)); assert ready.allowed and ready.stage==PromotionStage.MERGE_READY
