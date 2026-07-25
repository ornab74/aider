from __future__ import annotations
import difflib,os,tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Mapping
from aider.innovation_snapshot import RepositorySnapshot,RepositorySnapshotter
class TransactionState(str,Enum): OPEN='open'; SEALED='sealed'; COMMITTED='committed'; ROLLED_BACK='rolled-back'
@dataclass(frozen=True)
class TransactionPreview:
    changed_paths:tuple[str,...]; deleted_paths:tuple[str,...]; diff:str; baseline_digest:str; candidate_digest:str
class ShadowWorkspaceTransaction:
    def __init__(self,baseline_files:Mapping[str,str]):
        self.baseline_files={str(Path(k)):v for k,v in baseline_files.items()}; self._updates={}; self._deletions=set(); self.state=TransactionState.OPEN; self.snapshotter=RepositorySnapshotter(); self.baseline_snapshot=self.snapshotter.capture(self.baseline_files)
    def _open(self):
        if self.state!=TransactionState.OPEN: raise RuntimeError(f'transaction is {self.state.value}')
    def _norm(self,path):
        p=Path(path)
        if p.is_absolute() or '..' in p.parts: raise ValueError('transaction path must remain workspace-relative')
        return str(p)
    def stage(self,path,text): self._open(); p=self._norm(path); self._updates[p]=text; self._deletions.discard(p)
    def delete(self,path): self._open(); p=self._norm(path); self._updates.pop(p,None); self._deletions.add(p)
    def candidate_files(self):
        out=dict(self.baseline_files)
        for p in self._deletions: out.pop(p,None)
        out.update(self._updates); return out
    def preview(self):
        cand=self.candidate_files(); changed=tuple(sorted(p for p,t in cand.items() if self.baseline_files.get(p)!=t)); deleted=tuple(sorted(p for p in self.baseline_files if p not in cand)); diff=[]
        for p in sorted(set(changed)|set(deleted)): diff.extend(difflib.unified_diff(self.baseline_files.get(p,'').splitlines(True),cand.get(p,'').splitlines(True),fromfile=f'a/{p}',tofile=f'b/{p}'))
        return TransactionPreview(changed,deleted,''.join(diff),self.baseline_snapshot.aggregate_digest,self.snapshotter.capture(cand).aggregate_digest)
    def seal(self,expected_baseline:RepositorySnapshot|None=None):
        self._open(); exp=expected_baseline or self.baseline_snapshot
        if exp.aggregate_digest!=self.baseline_snapshot.aggregate_digest: raise RuntimeError('transaction baseline mismatch')
        self.state=TransactionState.SEALED; return self.preview()
    def commit(self,root,allow_write=False):
        if self.state!=TransactionState.SEALED: raise RuntimeError('transaction must be sealed before commit')
        prev=self.preview()
        if allow_write:
            root=Path(root).resolve()
            for rel in prev.deleted_paths:
                p=(root/rel).resolve()
                if p.exists():p.unlink()
            for rel,text in self._updates.items():
                p=(root/rel).resolve(); p.parent.mkdir(parents=True,exist_ok=True); fd,tmp=tempfile.mkstemp(prefix=f'.{p.name}.',dir=p.parent)
                with os.fdopen(fd,'w',encoding='utf-8') as h: h.write(text); h.flush(); os.fsync(h.fileno())
                os.replace(tmp,p)
            self.state=TransactionState.COMMITTED
        return prev
    def rollback(self):
        if self.state==TransactionState.COMMITTED: raise RuntimeError('committed transaction cannot roll back')
        self._updates.clear(); self._deletions.clear(); self.state=TransactionState.ROLLED_BACK
