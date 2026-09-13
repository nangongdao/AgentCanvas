"""Offline contract and shared side-effect classifier diagnostics; no execution/network."""
import json
from copy import deepcopy
from jsonschema import Draft202012Validator
from app.contract_compatibility import check_openapi_compatibility, ContractCompatibilityError
from app.schemas.dsl import NodeSpec
from app.services.execution_rerun import node_side_effect_reason

def api(schema):
    return {"openapi": "3.1.0", "info": {"title": "offline probe", "version": "1"},
        "paths": {"/probe": {"get": {"responses": {"200": {"description": "ok",
            "content": {"application/json": {"schema": schema}}}}}}}}

old = {"type": "object", "properties": {"id": {"type": "string"}}, "required": ["id"]}
new = deepcopy(old)
new["required"] = []
cases = [("required_response_removed", old, new, {}),
    ("response_enum_expanded", {"type": "string", "enum": ["ok"]}, {"type": "string", "enum": ["ok", "new"]}, "new")]
findings = []
for name, baseline_schema, new_schema, example in cases:
    blocked = False
    try:
        check_openapi_compatibility(api(baseline_schema), api(new_schema))
    except ContractCompatibilityError:
        blocked = True
    entry = {"case": name, "gate_blocked": blocked,
        "new_schema_accepts_example": Draft202012Validator(new_schema).is_valid(example),
        "old_schema_accepts_example": Draft202012Validator(baseline_schema).is_valid(example)}
    findings.append(entry)
    print(json.dumps(entry))
node = NodeSpec(id="offline_write", type="http", label="offline", config={"method": "POST", "url": "https://example.invalid/write"})
reason = node_side_effect_reason(node)
print(json.dumps({"case": "http_post_shared_side_effect_classifier", "reason": reason, "network_calls": 0}))
assert all(row["gate_blocked"] for row in findings) and reason is not None, "Contract directionality and HTTP write classification require review"
