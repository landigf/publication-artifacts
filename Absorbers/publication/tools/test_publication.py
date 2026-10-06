#!/usr/bin/env python3
"""Focused publication-boundary regressions without model/network calls."""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from common import ROOT,SWEEPS
sys.path.insert(0,str(ROOT))
from harness.make_absorbers_numbers import require_publication_inputs,validate_e8
from joint_witness_check import validate as joint_witness_validate


class PublicationBoundaryTests(unittest.TestCase):
    def chain(self):
        return json.loads((ROOT/"results-chain-deepseek/absorbers_chain.json").read_text())

    def test_certified_denominator_includes_unscoreable_rows(self):
        data=self.chain();cert=data["E8_decomposition"]["certificates"]
        scheme=cert["schemes"]["cert_eq"]
        scheme["certified_frac"]=round(scheme["certified_rows"]/(cert["rows"]-cert["unscoreable_rows"]),6)
        with self.assertRaisesRegex(ValueError,"denominator"):validate_e8(data)

    def test_cert_eq_rejects_observed_decision_change(self):
        data=self.chain();data["E8_decomposition"]["certificates"]["schemes"]["cert_eq"]["decision_changes"]=1
        with self.assertRaisesRegex(ValueError,"equality"):validate_e8(data)

    def test_saved_calls_use_two_times_all_rows(self):
        data=self.chain();cert=data["E8_decomposition"]["certificates"]
        scheme=cert["schemes"]["cert_eq"]
        scheme["calls_saved_frac"]=round(sum(scheme["step_calls_reused"].values())/(2*(cert["rows"]-cert["unscoreable_rows"])),6)
        with self.assertRaisesRegex(ValueError,"denominator"):validate_e8(data)

    def test_missing_required_sweep_is_not_skipped(self):
        with tempfile.TemporaryDirectory(prefix="absorbers-missing-sweep-") as temporary:
            root=Path(temporary)
            for directory in list(SWEEPS)[:-1]:
                target=root/directory;target.mkdir();(target/"raw").mkdir()
                name="absorbers_chain.json" if "chain" in directory else "absorbers.json"
                (target/name).write_bytes((ROOT/directory/name).read_bytes())
                (target/"decisions.jsonl").write_text("")
            with self.assertRaisesRegex(FileNotFoundError,"results-chain-phi4"):require_publication_inputs(root)

    def test_requires_all_sixty_five_requests(self):
        with tempfile.TemporaryDirectory(prefix="absorbers-incomplete-sweep-") as temporary:
            root=Path(temporary);target=root/"results";target.mkdir();(target/"raw").mkdir()
            (target/"decisions.jsonl").write_text("");(target/"absorbers.json").write_text(json.dumps({"n_requests":64}))
            with self.assertRaisesRegex(ValueError,"65"):require_publication_inputs(root)

    def test_joint_witness_grid_and_recorded_numeric_domain(self):
        actual=joint_witness_validate()
        expected=json.loads((ROOT/"publication/reports/joint-witness-validation.json").read_text())
        self.assertEqual(actual,expected)
        self.assertEqual(actual["numeric_domain"]["violations"],0)

    def test_relative_environment_interpreter_is_rooted_before_directory_change(self):
        with tempfile.TemporaryDirectory(prefix="absorbers-relative-interpreter-") as temporary:
            root=Path(temporary)
            publication=root/"publication";publication.mkdir()
            shutil.copy2(ROOT/"publication/REPRODUCE.sh",publication/"REPRODUCE.sh")
            executable=root/".venv/bin/python";executable.parent.mkdir(parents=True)
            executable.write_text("#!/bin/sh\nset -eu\n[ \"$1\" = tools/reproduce.py ]\n[ \"$(basename \"$PWD\")\" = publication ]\nprintf 'relative interpreter invoked\\n'\n")
            executable.chmod(0o755)
            environment=os.environ.copy();environment["ABSORBERS_PYTHON"]=".venv/bin/python"
            result=subprocess.run(["sh","publication/REPRODUCE.sh","--offline"],cwd=root,env=environment,text=True,capture_output=True)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(result.stdout,"relative interpreter invoked\n")


if __name__=="__main__":unittest.main()
