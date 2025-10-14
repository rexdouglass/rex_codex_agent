rex_repo_root(){ git rev-parse --show-toplevel 2>/dev/null || pwd; }
rex_repo_doctor(){ command -v python3 && python3 --version || true; command -v node && node --version || true; command -v docker && docker --version || true; }
rex_json_get(){ python3 - "$1" "$2" <<'PY'
import json,sys; d=json.load(open(sys.argv[1])); v=d
for p in sys.argv[2].split("."): v=v[p]
print(v if not isinstance(v,(dict,list)) else json.dumps(v))
PY
}

rex_current_feature_slug(){
  local root; root="$(rex_repo_root)"
  python3 - "$root/rex-agent.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
    feature = data.get("feature", {})
    slug = feature.get("active_slug")
    if slug:
        print(slug)
except FileNotFoundError:
    pass
PY
}

rex_current_feature_card(){
  local root; root="$(rex_repo_root)"
  python3 - "$root/rex-agent.json" <<'PY'
import json, sys
try:
    with open(sys.argv[1]) as fh:
        data = json.load(fh)
    feature = data.get("feature", {})
    card = feature.get("active_card")
    if card:
        print(card)
except FileNotFoundError:
    pass
PY
}
