from __future__ import annotations
import hashlib,json
from dataclasses import asdict,dataclass
from datetime import datetime,timezone
from pathlib import Path
from typing import Mapping

def _sha(text): return hashlib.sha256(text.encode()).hexdigest()
@dataclass(frozen=True)
class RepositorySnapshot:
    aggregate_digest:str; file_digests:Mapping[str,str]; file_sizes:Mapping[str,int]; created_at:str
    def save(self,path): Path(path).write_text(json.dumps(asdict(self),indent=2,sort_keys=True))
    @classmethod
    def load(cls,path): return cls(**json.loads(Path(path).read_text()))
@dataclass(frozen=True)
class SnapshotDelta:
    added:tuple[str,...]; removed:tuple[str,...]; modified:tuple[str,...]; unchanged:tuple[str,...]; drift_score:float
    @property
    def clean(self): return not self.added and not self.removed and not self.modified
class RepositorySnapshotter:
    def capture(self,files:Mapping[str,str],**_):
        dig={str(Path(k)):_sha(v) for k,v in sorted(files.items())}; sizes={str(Path(k)):len(v.encode()) for k,v in sorted(files.items())}
        agg=_sha(json.dumps(dig,sort_keys=True,separators=(',',':')))
        return RepositorySnapshot(agg,dig,sizes,datetime.now(timezone.utc).isoformat())
    def compare(self,a,b):
        before=set(a.file_digests); after=set(b.file_digests); shared=before&after
        added=tuple(sorted(after-before)); removed=tuple(sorted(before-after)); modified=tuple(sorted(k for k in shared if a.file_digests[k]!=b.file_digests[k])); unchanged=tuple(sorted(shared-set(modified)))
        weight=sum(b.file_sizes.get(k,a.file_sizes.get(k,0)) for k in (*added,*removed,*modified)); total=max(1,sum(a.file_sizes.values())+sum(b.file_sizes.values()))
        return SnapshotDelta(added,removed,modified,unchanged,min(1.0,2*weight/total))
    def verify(self,snapshot,files): return self.compare(snapshot,self.capture(files))
