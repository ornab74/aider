from __future__ import annotations
import hashlib,json
from dataclasses import asdict,dataclass
from pathlib import Path
from typing import Mapping
from aider.innovation_snapshot import RepositorySnapshot,RepositorySnapshotter

def _dig(payload): return hashlib.sha256(json.dumps(payload,sort_keys=True,separators=(',',':'),default=str).encode()).hexdigest()
@dataclass(frozen=True)
class ReplayEvent:
    sequence:int; event_type:str; payload:Mapping[str,object]; payload_digest:str; before_digest:str; after_digest:str; previous_event_digest:str; event_digest:str
@dataclass(frozen=True)
class ReplayLog:
    task:str; baseline_digest:str; events:tuple[ReplayEvent,...]
    def save(self,path): Path(path).write_text(json.dumps(asdict(self),indent=2,sort_keys=True))
    @classmethod
    def load(cls,path):
        p=json.loads(Path(path).read_text()); p['events']=tuple(ReplayEvent(**e) for e in p['events']); return cls(**p)
@dataclass(frozen=True)
class ReplayVerification:
    valid:bool; checked_events:int; issues:tuple[str,...]; final_digest:str
class DeterministicReplayRecorder:
    def __init__(self,task:str,baseline:RepositorySnapshot): self.task=task; self.baseline=baseline; self.events=[]
    def record(self,event_type,payload,before,after):
        seq=len(self.events)+1; pd=_dig(payload); prev=self.events[-1].event_digest if self.events else self.baseline.aggregate_digest
        ed=self._event_digest(seq,event_type,pd,before.aggregate_digest,after.aggregate_digest,prev)
        e=ReplayEvent(seq,event_type,dict(payload),pd,before.aggregate_digest,after.aggregate_digest,prev,ed); self.events.append(e); return e
    def build(self): return ReplayLog(self.task,self.baseline.aggregate_digest,tuple(self.events))
    @staticmethod
    def _event_digest(seq,typ,pd,before,after,prev): return hashlib.sha256('\0'.join(map(str,(seq,typ,pd,before,after,prev))).encode()).hexdigest()
class DeterministicReplayVerifier:
    def verify(self,log):
        issues=[]; before=log.baseline_digest; prev=log.baseline_digest
        for i,e in enumerate(log.events,1):
            if e.sequence!=i: issues.append(f'event {i} sequence mismatch')
            if e.before_digest!=before: issues.append(f'event {i} does not chain')
            if e.previous_event_digest!=prev: issues.append(f'event {i} previous digest invalid')
            if e.payload_digest!=_dig(e.payload): issues.append(f'event {i} payload invalid')
            exp=DeterministicReplayRecorder._event_digest(e.sequence,e.event_type,e.payload_digest,e.before_digest,e.after_digest,e.previous_event_digest)
            if e.event_digest!=exp: issues.append(f'event {i} digest invalid')
            before=e.after_digest; prev=e.event_digest
        final=log.events[-1].after_digest if log.events else log.baseline_digest
        return ReplayVerification(not issues,len(log.events),tuple(issues),final)
    def verify_final_files(self,log,files):
        r=self.verify(log); current=RepositorySnapshotter().capture(files).aggregate_digest; issues=list(r.issues)
        if current!=r.final_digest: issues.append('current repository does not match replay final digest')
        return ReplayVerification(not issues,r.checked_events,tuple(issues),current)
