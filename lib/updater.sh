rex_self_update(){
  [[ "${REX_AGENT_NO_UPDATE:-0}" == "1" ]] && return 0
  git -C "$REX_SRC" fetch --all --tags --prune || return 0
  local channel="${REX_AGENT_CHANNEL:-stable}"
  case "$channel" in
    stable) tag="$(git -C "$REX_SRC" describe --tags --abbrev=0 2>/dev/null || echo main)"; git -C "$REX_SRC" checkout -q "$tag" || true ;;
    main)   git -C "$REX_SRC" checkout -q main && git -C "$REX_SRC" pull --ff-only || true ;;
    *)      git -C "$REX_SRC" checkout -q "$channel" || true ;;
  esac
}
