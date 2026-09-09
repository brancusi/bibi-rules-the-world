#!/usr/bin/env bash
# Opt-in real lifecycle regression. Every Herdr lifecycle/state call is routed
# through the generated non-default-session helper; the default is tripwired.
set -euo pipefail

if [[ ${BIBI_HERDR_LIFECYCLE_E2E:-0} != 1 ]]; then
  echo "SKIP Herdr/Pi lifecycle E2E (set BIBI_HERDR_LIFECYCLE_E2E=1)"
  exit 0
fi

ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)
HERDR_LAB_HELPER=${HERDR_LAB_HELPER:-/home/bibi/firstmate/bin/fm-herdr-lab.sh}
[[ -x "$HERDR_LAB_HELPER" ]] || { echo "missing named Herdr lab helper" >&2; exit 1; }
[[ $(pi --version) == 0.85.1 ]] || { echo "this regression is pinned to Pi 0.85.1" >&2; exit 1; }
[[ $(herdr --version) == *0.7.4* ]] || { echo "this regression is pinned to Herdr 0.7.4" >&2; exit 1; }
command -v jq >/dev/null

SESSION=$("$HERDR_LAB_HELPER" name firstmate-portable-setup-audit-a1)
TMP=$(mktemp -d "$ROOT/.herdr-pi-lifecycle.XXXXXX")
cleanup() {
  "$HERDR_LAB_HELPER" teardown "$SESSION"
  rm -rf "$TMP"
}
trap cleanup EXIT
mkdir -p "$TMP/no-integration/extensions" "$TMP/with-integration/extensions"

cat > "$TMP/offline-provider.ts" <<'TS'
import { writeFileSync } from "node:fs";
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
import { createAssistantMessageEventStream } from "@earendil-works/pi-ai";
export default function (pi: ExtensionAPI) {
  pi.registerProvider("offline-lifecycle", {
    name: "Offline lifecycle fixture", baseUrl: "http://127.0.0.1.invalid", apiKey: "offline", api: "offline-lifecycle" as any,
    models: [{id:"slow",name:"Slow",reasoning:false,input:["text"],cost:{input:0,output:0,cacheRead:0,cacheWrite:0},contextWindow:10000,maxTokens:100}],
    streamSimple(model: any) {
      const stream = createAssistantMessageEventStream();
      const output: any = {role:"assistant",content:[],api:model.api,provider:model.provider,model:model.id,usage:{input:0,output:0,cacheRead:0,cacheWrite:0,totalTokens:0,cost:{input:0,output:0,cacheRead:0,cacheWrite:0,total:0}},stopReason:"pending",timestamp:Date.now()};
      (async () => {
        writeFileSync(process.env.LAB_WORKING_MARKER!, "working\n");
        stream.push({type:"start",partial:output});
        await new Promise(resolve => setTimeout(resolve, 5000));
        output.content.push({type:"text",text:"offline ok"}); output.stopReason="stop";
        stream.push({type:"done",reason:"stop",message:output}); stream.end();
        writeFileSync(process.env.LAB_DONE_MARKER!, "done\n");
      })();
      return stream;
    }
  });
}
TS

wait_file() {
  local path=$1 attempt
  for ((attempt = 0; attempt < 100; attempt++)); do
    [[ -f "$path" ]] && return 0
    sleep 0.1
  done
  echo "timed out waiting for $path" >&2; return 1
}

agent_state() {
  "$HERDR_LAB_HELPER" run "$SESSION" agent get "$1" 2>/dev/null \
    | jq -r '.result.agent.agent_status // empty' 2>/dev/null || true
}

wait_state() {
  local pane=$1 wanted=$2 attempt state
  for ((attempt = 0; attempt < 100; attempt++)); do
    state=$(agent_state "$pane")
    [[ "$state" == "$wanted" ]] && return 0
    sleep 0.1
  done
  echo "pane $pane did not reach $wanted (last ${state:-missing})" >&2; return 1
}

launch_fixture() {
  local pi_home=$1 prefix=$2 created pane command
  created=$("$HERDR_LAB_HELPER" run "$SESSION" workspace create --cwd "$ROOT" \
    --label "$prefix" --env "PI_CODING_AGENT_DIR=$pi_home" --env PI_OFFLINE=1 \
    --env "LAB_WORKING_MARKER=$TMP/$prefix.working" --env "LAB_DONE_MARKER=$TMP/$prefix.done" --no-focus)
  pane=$(jq -r '.result.root_pane.pane_id' <<<"$created")
  command="pi --offline --no-context-files --no-session -e '$TMP/offline-provider.ts' --provider offline-lifecycle --model slow 'exercise lifecycle'"
  "$HERDR_LAB_HELPER" run "$SESSION" pane run "$pane" "$command" >/dev/null
  printf '%s\n' "$pane"
}

"$HERDR_LAB_HELPER" provision "$SESSION"

# Negative control: the provider's independent marker proves Pi is actively
# streaming while the degraded current screen manifest calls it idle.
negative_pane=$(launch_fixture "$TMP/no-integration" negative)
wait_file "$TMP/negative.working"
wait_state "$negative_pane" idle
explain=$("$HERDR_LAB_HELPER" run "$SESSION" agent explain "$negative_pane" --json)
jq -e '.fallback_reason == "default_known_agent_idle_fallback" or .result.explanation.fallback_reason == "default_known_agent_idle_fallback" or .result.fallback_reason == "default_known_agent_idle_fallback"' \
  <<<"$explain" >/dev/null || { echo "negative control did not expose the known idle fallback" >&2; exit 1; }

# Positive control: install the bundled integration into a separate temporary
# Pi home, then prove idle -> working -> idle around the same offline stream.
PI_CODING_AGENT_DIR="$TMP/with-integration" "$HERDR_LAB_HELPER" run "$SESSION" integration install pi >/dev/null
PI_CODING_AGENT_DIR="$TMP/with-integration" "$HERDR_LAB_HELPER" run "$SESSION" integration status \
  | grep -q '^pi: current '
positive_pane=$(launch_fixture "$TMP/with-integration" positive)
wait_file "$TMP/positive.working"
wait_state "$positive_pane" working
wait_file "$TMP/positive.done"
# Herdr exposes an unfocused, unseen idle as the equivalent native `done`
# presentation. Focus/acknowledge the isolated pane so the final state is the
# requested seen-idle value rather than that presentation alias.
"$HERDR_LAB_HELPER" run "$SESSION" agent focus "$positive_pane" >/dev/null
wait_state "$positive_pane" idle

echo "PASS Herdr/Pi negative idle fallback and integrated idle-working-idle lifecycle"
