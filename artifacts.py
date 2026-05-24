from pathlib import Path
from hashlib import sha256
from schema import Artifact

class ArtifactStore:
    def __init__(self, root: Path):
        self.artifacts_path = root
        self.artifacts_path.mkdir(parents=True, exist_ok=True)
        

    def put(self, blob: bytes ,content_type: str, source: str, descriptor: str) -> str:
        sha = sha256(blob).hexdigest()
        aid = f"art:{sha[:12]}"   # matches schema
        p = self.artifacts_path / aid
        if not p.exists():
            p.write_bytes(blob)
        meta = Artifact(
            id=aid,
            content_type=content_type,
            size_bytes=len(blob),
            source=source,
            descriptor=descriptor,
        )
        meta_path = self.artifacts_path / f"{aid}.meta.json"
        meta_path.write_text(meta.model_dump_json(indent=2), encoding="utf-8")
        return aid

    def get_bytes(self, artifact_id: str) -> bytes:
        p = self.artifacts_path / artifact_id

        if not p.exists():
            raise FileNotFoundError(f"Artifact {artifact_id} not found")

        return p.read_bytes()

    def get_meta(self, artifact_id: str) -> Artifact:
        meta_path = self.artifacts_path / f"{artifact_id}.meta.json"
        if not meta_path.exists():
            raise FileNotFoundError(f"Artifact {artifact_id} not found")
        return Artifact.model_validate_json(meta_path.read_text(encoding="utf-8"))

    def exists(self, artifact_id: str) -> bool:
        return (self.artifacts_path / artifact_id).exists()

if __name__ == '__main__':
    store = ArtifactStore(Path(__file__).parent / "state" / "artifacts")
    aid = store.put(b"hello world", "text/plain", "manual", "test")
    print(store.get_bytes(aid))
    print(store.get_meta(aid))
    print(store.exists(aid))