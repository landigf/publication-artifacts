#!/usr/bin/env python3
"""Finite joint-grid regression for the retained certificate consumer.

The independently chosen richer numeric grid checks this frozen workload's
contexts. Passing is empirical coverage evidence, not a general proof that
the production witness enumeration covers every real-valued input.
"""
from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from pathlib import Path

from common import ROOT,SWEEPS
sys.path.insert(0,str(ROOT))
from chain.absorbers_chain import ChainSweep,STEP_OUTPUTS
from chain.certificates import DECISION_INERT_FIELDS,decision,joint_witness_records
from taskgen.schema import CATEGORY_THRESHOLDS


def around(values) -> list[float]:
    output=set()
    for value in values:
        value=float(value)
        output.add(value)
        for direction in [-math.inf,math.inf]:
            adjacent=math.nextafter(value,direction)
            if math.isfinite(adjacent) and adjacent>=0:output.add(adjacent)
    return sorted(output)


def reference_parse_grid() -> list[dict]:
    records=[]
    for category in [*CATEGORY_THRESHOLDS,"unknown_joint_grid_category"]:
        ceiling=float(CATEGORY_THRESHOLDS.get(category,1000))
        budgets=around([0,1,*[ceiling*fraction for fraction in [.01,.2,.25,.5,.9,1,1.1,2,10]]])
        for budget in budgets:
            binding=min(ceiling,budget)
            amounts=around([0,1,budget,ceiling,.25*binding,.9*binding,.5*binding,2*max(ceiling,budget)])
            for amount,dp,sr in itertools.product(amounts,[False,True],[False,True]):
                records.append({"category":category,"amount":amount,"budget_remaining":budget,"data_processing":dp,"security_review":sr})
    return records


def validate(root: Path = ROOT) -> dict:
    combinations=[("c_parse",),("c_justify",),("c_parse","c_justify")]
    contexts={steps:{} for steps in combinations}
    numeric=0;valid_rows=0;sweep_counts=[]
    for directory in SWEEPS:
        if "chain" not in directory:continue
        sweep=ChainSweep(root/directory,check_cache=False)
        local_numeric=local_valid=0
        for row in sweep.rows:
            fields={step:sweep.step_fields(row,step) for step in STEP_OUTPUTS}
            parsed=fields["c_parse"]
            if parsed is not None:
                record=dict(parsed)
                for field in ["amount","budget_remaining"]:
                    value=record[field]
                    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<0:
                        raise ValueError(f"Retained schema-valid parse violates finite/nonnegative domain in {directory}")
                    numeric+=1;local_numeric+=1
            if any(value is None for value in fields.values()):continue
            request=sweep.perturbed(row["base_request_id"],row["salt"]) if row["phase"]=="pert" else sweep.requests[row["base_request_id"]]
            record={**dict(fields["c_parse"]),**dict(fields["c_justify"])}
            truth=request.structured_v2()
            for field in ["supplier_approved","supplier_risk","supplier_sanctioned"]:record[field]=truth[field]
            valid_rows+=1;local_valid+=1
            for steps in combinations:
                varied=set().union(*(STEP_OUTPUTS[step] for step in steps))
                fixed=tuple(sorted((key,json.dumps(value,sort_keys=True)) for key,value in record.items() if key not in varied and key not in DECISION_INERT_FIELDS))
                contexts[steps].setdefault(fixed,record)
        sweep_counts.append({"sweep":directory,"schema_valid_numeric_fields":local_numeric,"both_feeding_records_valid_rows":local_valid})
    parse_grid=reference_parse_grid()
    tested=0;summary=[]
    for steps,entries in contexts.items():
        counts={};reference_evaluations=0;production_evaluations=0
        for record in entries.values():
            production=set()
            for witness in joint_witness_records(list(steps),record):
                production.add(decision(witness));production_evaluations+=1
            richer=set()
            parse_values=parse_grid if "c_parse" in steps else [{}]
            adequate_values=[False,True] if "c_justify" in steps else [record["justification_adequate"]]
            for parsed,adequate in itertools.product(parse_values,adequate_values):
                richer.add(decision({**record,**parsed,"justification_adequate":adequate}))
                reference_evaluations+=1
                if not richer <= production:
                    raise ValueError("Richer joint grid found a consumer decision absent from production witnesses")
            if richer!=production:raise ValueError("Production witnesses found a decision absent from the independent richer grid")
            key=",".join(sorted(production));counts[key]=counts.get(key,0)+1;tested+=1
        summary.append({"candidate_steps":list(steps),"distinct_fixed_contexts":len(entries),"production_witness_evaluations":production_evaluations,"richer_grid_evaluations":reference_evaluations,"decision_set_context_counts":counts})
    return {"schema_version":1,"status":"passed","scope":"All retained canonical/repeated/perturbed chain rows with both feeding records valid; memoized fixed consumer contexts after removing the two separately tested decision-inert fields. Three candidate-step combinations. Finite regression evidence for the frozen policy and workload, not a general formal completeness proof.","numeric_domain":{"assumed_domain":"finite nonnegative amount and budget_remaining","schema_valid_numeric_fields_checked":numeric,"violations":0},"valid_combined_rows":valid_rows,"parse_reference_grid_records":len(parse_grid),"reference_grid":"Seven category classes including unknown; all four compliance Boolean pairs; budget anchors at zero, one, and .01/.2/.25/.5/.9/1/1.1/2/10 times the category ceiling; amounts at zero, one, budget, ceiling, .25/.9/.5 of the binding threshold, and beyond both. Each anchor includes adjacent representable floating-point values below and above when finite/nonnegative. justification_adequate varies jointly for both-step candidates.","memoized_contexts_tested":tested,"sweeps":sweep_counts,"candidate_combinations":summary}


if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output",type=Path)
    args=parser.parse_args();result=validate()
    if args.output:
        args.output.parent.mkdir(parents=True,exist_ok=True);args.output.write_text(json.dumps(result,indent=2,sort_keys=True)+"\n")
    print(f"Joint witness regression passed {result['memoized_contexts_tested']} contexts and {sum(item['richer_grid_evaluations'] for item in result['candidate_combinations']):,} richer-grid evaluations; {result['numeric_domain']['schema_valid_numeric_fields_checked']:,} finite nonnegative recorded numeric fields.")
