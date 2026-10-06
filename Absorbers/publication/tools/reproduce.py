#!/usr/bin/env python3
"""Reproduce all six frozen sweeps in a disposable tree, never live."""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

from common import PUBLICATION,ROOT,SWEEPS,sha

sys.path.insert(0,str(ROOT))
from harness.make_absorbers_numbers import require_publication_inputs,validate_e8

CREDENTIAL_FIELDS={"api_key","authorization","proxy-authorization","cookie","set-cookie","x-api-key","x-goog-api-key","x-amz-security-token","x-auth-token","x-access-token","password","secret","access_token"}


def require(condition: bool,message: str) -> None:
    if not condition:raise ValueError(message)


def credential_fields(value) -> int:
    if isinstance(value,dict):
        return sum(str(key).lower() in CREDENTIAL_FIELDS and bool(item) for key,item in value.items()) + sum(credential_fields(item) for item in value.values())
    if isinstance(value,list):return sum(credential_fields(item) for item in value)
    return 0


def integrity() -> dict:
    require_publication_inputs(ROOT)
    manifest=json.loads((PUBLICATION/"artifact_snapshot.json").read_text())
    require(manifest["required_sweeps"]==list(SWEEPS),"Incomplete required sweep inventory")
    seen=set()
    for item in manifest["files"]:
        relative=Path(item["path"])
        require(not relative.is_absolute() and ".." not in relative.parts,"Unsafe manifest path")
        require(item["path"] not in seen,"Duplicate manifest path")
        seen.add(item["path"]);path=ROOT/relative
        require(path.is_file() and not path.is_symlink(),"Missing/symlinked artifact file")
        require(path.stat().st_size==item["bytes"] and sha(path)==item["sha256"],f"Hash mismatch: {item['path']}")
    from taskgen.generator import generate
    from chain.taskgen import generate_chain
    require(len(generate(42))==len(generate_chain(42))==65,"Synthetic request generator count differs")
    credential_count=0;raw_files=0;prompt_records=0;provider_record_fields=set()
    for directory in SWEEPS:
        for path in (ROOT/directory/"raw").glob("*.json"):
            raw_files+=1;data=json.loads(path.read_text());credential_count+=credential_fields(data)
            provider_record_fields.update(data)
            prompt_records+=isinstance(data.get("call_spec"),dict)
    require(credential_count==0,"Credential field detected in frozen raw cache; no values printed")
    return {"hashed_files":len(manifest["files"]),"raw_cache_files":raw_files,"prompt_storing_cache_records":prompt_records,"credential_fields":credential_count,"raw_top_level_field_names":sorted(provider_record_fields),"six_sweeps_present":True}


def run(command: list[str],cwd: Path,environment: dict) -> str:
    process=subprocess.run(command,cwd=cwd,env=environment,text=True,stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    if process.returncode:
        # Logs contain synthetic request IDs and source-level errors, not credentials.
        print(process.stdout[-10000:],file=sys.stderr)
        raise ValueError(f"Offline command failed: {' '.join(command[:4])}")
    return process.stdout


def main() -> None:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check",action="store_true",help="Integrity validation without replay")
    parser.add_argument("--output-dir",type=Path,default=PUBLICATION/"reports/reproduction")
    args=parser.parse_args()
    checks=integrity()
    if args.check:
        print(f"Verified {checks['hashed_files']:,} hashes and all six complete sweep inputs.")
        return
    output=args.output_dir.resolve();output.mkdir(parents=True,exist_ok=True)
    environment=dict(os.environ);environment["PYTHONHASHSEED"]="0"
    with tempfile.TemporaryDirectory(prefix="absorbers-publication-offline-") as temporary:
        work=Path(temporary)
        environment["MPLCONFIGDIR"]=str(work/"mpl-cache")
        environment["PYTHONPYCACHEPREFIX"]=str(work/"pycache")
        for folder in ["agents","taskgen","chain","harness","tests","runtime"]:
            shutil.copytree(ROOT/folder,work/folder,ignore=shutil.ignore_patterns("__pycache__","*.pyc"))
        # Existing script-boundary tests use only help/rejected arguments.
        # Supply the publication entrypoint without executing the historical one.
        shutil.copy2(PUBLICATION/"REPRODUCE.sh",work/"REPRODUCE.sh")
        for index,(directory,backend) in enumerate(SWEEPS.items(),start=1):
            print(f"[{index}/6] copying and replaying {directory} from its frozen cache",flush=True)
            source=ROOT/directory;target=work/directory
            shutil.copytree(source/"raw",target/"raw",ignore=shutil.ignore_patterns("failed","__pycache__"))
            chain="chain" in directory
            expected_analysis="absorbers_chain.json" if chain else "absorbers.json"
            for name in ["decisions.jsonl",expected_analysis]+(["chain_meta.json"] if chain else ["metrics.json"]):shutil.copy2(source/name,target/name)
            runner="chain.runner" if chain else "harness.runner"
            run([sys.executable,"-m",runner,"--backend",backend,"--from-cache","--out",str(target)],work,environment)
            require((target/"decisions.jsonl").read_bytes()==(source/"decisions.jsonl").read_bytes(),f"Decision-file byte mismatch in {directory}")
            if chain:require(json.loads((target/"chain_meta.json").read_text())==json.loads((source/"chain_meta.json").read_text()),f"Chain metadata mismatch in {directory}")
            else:
                run([sys.executable,"-m","harness.metrics","--results",str(target)],work,environment)
                require(json.loads((target/"metrics.json").read_text())==json.loads((source/"metrics.json").read_text()),f"Main metrics metadata mismatch in {directory}")
            module="chain.absorbers_chain" if chain else "harness.absorbers"
            run([sys.executable,"-m",module,"--results",str(target)],work,environment)
            a=json.loads((target/expected_analysis).read_text());b=json.loads((source/expected_analysis).read_text())
            require(a==b,f"Analysis JSON mismatch in {directory}")
            if chain:validate_e8(a)
        print("Regenerating E8 tables, macros, certificate and canary figures",flush=True)
        report_dir=work/"publication/reports";figure_dir=work/"publication/figures"
        run([sys.executable,"-m","harness.make_absorbers_numbers","--publication","--output",str(report_dir/"numbers.tex")],work,environment)
        run([sys.executable,"-m","harness.absorbers_plots","--publication","--output-dir",str(figure_dir),"--reports-dir",str(report_dir)],work,environment)
        for path in report_dir.iterdir():
            require(path.read_bytes()==(PUBLICATION/"reports"/path.name).read_bytes(),f"Generated report/table drift: {path.name}")
            shutil.copy2(path,output/path.name)
        (output/"figures").mkdir(exist_ok=True)
        for path in figure_dir.iterdir():
            require(sha(path)==sha(PUBLICATION/"figures"/path.name),f"Generated figure drift: {path.name}")
            shutil.copy2(path,output/"figures"/path.name)
        print("Running existing offline suites and publication boundary tests",flush=True)
        test_output=run([sys.executable,"-m","unittest","discover","-s","tests"],work,environment)
        boundary_output=run([sys.executable,str(PUBLICATION/"tools/test_publication.py")],ROOT,environment)
        test_counts={"existing_unittest_discovery":int(re.search(r"Ran (\d+) tests?",test_output).group(1)),"publication_boundary":int(re.search(r"Ran (\d+) tests?",boundary_output).group(1))}
    checks.update({"status":"passed","python":sys.version.split()[0],"test_counts":test_counts,"checks":["all six decision files byte-identical","all six analysis JSON values identical","three chain metadata values identical","three main metrics metadata values identical","E8 cone/equality/safety/denominator invariants","generated macros/tables/JSON byte-identical","all six PDF/PNG files byte-identical","existing offline unittest discovery","publication missing-sweep/denominator/certificate/relative-interpreter tests","joint witness richer-grid equality and finite nonnegative recorded numeric domain"]})
    (output/"verification.json").write_text(json.dumps(checks,indent=2)+"\n")
    print("All six frozen sweeps and publication artifacts reproduced successfully.")


if __name__ == "__main__":
    try:main()
    except (ValueError,FileNotFoundError) as error:
        print(f"Publication verification failed: {error}",file=sys.stderr)
        raise SystemExit(1)
