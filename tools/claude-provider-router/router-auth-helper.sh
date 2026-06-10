#!/bin/bash
# apiKeyHelper：
# - 当 ANTHROPIC_BASE_URL 指向本地路由代理（127.0.0.1）时，三态判断：
#   - team 模式 leader（CLAUDE_TEAM_ROLE=leader）→ 返回 "leader-token"
#   - team 模式 teammate（无 CLAUDE_TEAM_ROLE，被 claude 过滤）→ 返回 "teammate-token"
#   - 协议转换模式（CLAUDE_PROTOCOL=openai 等）→ 透传真实 CLAUDE_AUTH_TOKEN
# - 否则（直连远端 provider）→ 透传真实 token

if [[ "$ANTHROPIC_BASE_URL" == *"127.0.0.1"* ]]; then
    if [[ "$CLAUDE_TEAM_ROLE" == "leader" ]]; then
        # team 模式 leader
        echo "leader-token"
    elif [[ -n "$CLAUDE_PROTOCOL" ]]; then
        # 协议转换模式：透传真实 token，让 router 用它转发给上游
        echo "$CLAUDE_AUTH_TOKEN"
    else
        # team 模式 teammate
        echo "teammate-token"
    fi
else
    echo "$CLAUDE_AUTH_TOKEN"
fi
