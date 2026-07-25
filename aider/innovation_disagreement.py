from __future__ import annotations
import re
from collections import Counter,defaultdict
from dataclasses import dataclass
from typing import Iterable
@dataclass(frozen=True)
class VerifierVerdict:
    verifier:str; patch_digest:str; approved:bool; confidence:float; findings:tuple[str,...]=()
@dataclass(frozen=True)
class VerdictCluster:
    decision:str; verifiers:tuple[str,...]; mean_confidence:float
@dataclass(frozen=True)
class DisagreementReport:
    consensus:str; agreement_ratio:float; clusters:tuple[VerdictCluster,...]; disputed_findings:tuple[str,...]; outlier_verifiers:tuple[str,...]; patch_mismatch:bool; required_action:str
class VerifierDisagreementAnalyzer:
    def analyze(self,verdicts:Iterable[VerifierVerdict]):
        items=tuple(verdicts)
        if not items:return DisagreementReport('no-verdicts',0,(),(),(),False,'request verifier evidence')
        mismatch=len({x.patch_digest for x in items})>1; grouped=defaultdict(list)
        for x in items: grouped[x.approved].append(x)
        weighted={k:sum(x.confidence for x in v) for k,v in grouped.items()}; winner=max(weighted,key=weighted.get); ratio=len(grouped[winner])/len(items)
        clusters=tuple(VerdictCluster('approve' if k else 'reject',tuple(sorted(x.verifier for x in v)),sum(x.confidence for x in v)/len(v)) for k,v in sorted(grouped.items(),reverse=True))
        cons='patch-mismatch' if mismatch else ('approve' if len(grouped)==1 and winner else 'reject' if len(grouped)==1 else 'split')
        counts=Counter(re.sub(r'\s+',' ',f.strip().lower()) for x in items for f in x.findings if f.strip()); disputed=tuple(sorted(f for f,c in counts.items() if c<len(items)))
        outliers=tuple(sorted(x.verifier for x in items if x.approved!=winner and x.confidence>=.5))
        action='normalize all verdicts onto one patch digest' if mismatch else 'request an independent arbiter and targeted evidence' if cons=='split' else 'return patch to repair' if cons=='reject' else 'consensus is sufficient for the current gate'
        return DisagreementReport(cons,ratio,clusters,disputed,outliers,mismatch,action)
