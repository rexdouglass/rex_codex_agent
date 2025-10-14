rex_repo_root(){ git rev-parse --show-toplevel 2>/dev/null || pwd; }
rex_repo_doctor(){ command -v python3 && python3 --version || true; command -v node && node --version || true; command -v docker && docker --version || true; }
rex_json_get(){ python3 - "$1" "$2" <<'PY'
import json,sys; d=json.load(open(sys.argv[1])); v=d
for p in sys.argv[2].split("."): v=v[p]
print(v if not isinstance(v,(dict,list)) else json.dumps(v))
PY
}
