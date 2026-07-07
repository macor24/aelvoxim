#!/usr/bin/env bash
# Aelvoxim git pre-commit hook
# 安装: 把这个文件复制到 .git/hooks/pre-commit 并 chmod +x
#
# 作用: 每次 git commit 前自动检查：
#   - HTML 内 JS 符号平衡
#   - Python import
#   - 阻止有问题的代码被提交

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/../.." && pwd)"
echo "🔍 Aelvoxim pre-commit check..."

# 只检查本次改动的 HTML 文件
CHANGED_HTML=$(git diff --cached --name-only --diff-filter=ACM | grep '\.html$' || true)
CHANGED_PY=$(git diff --cached --name-only --diff-filter=ACM | grep '\.py$' || true)

HAS_ERROR=0

# ── HTML 检查 ──
if [ -n "$CHANGED_HTML" ]; then
    for f in $CHANGED_HTML; do
        full="$ROOT_DIR/$f"
        if [ ! -f "$full" ]; then continue; fi

        # JS 大括号平衡
        SCRIPT_CONTENT=$(python3 -c "
import re
try:
    content = open('$full').read()
    scripts = re.findall(r'<script>(.*?)</script>', content, re.DOTALL)
    issues = []
    for idx, s in enumerate(scripts):
        if s.count('{') != s.count('}'):
            issues.append(f'  <script>#{idx+1}: 大括号不平衡 ({\"{\"}=\${s.count(chr(123))}, \"}\"={s.count(chr(125))})')
        if s.count('(') != s.count(')'):
            issues.append(f'  <script>#{idx+1}: 圆括号不平衡')
    if issues:
        print('\\n'.join(issues))
except Exception:
    pass
" 2>/dev/null || true)

        if [ -n "$SCRIPT_CONTENT" ]; then
            echo "  ✗ $f: JS 语法问题"
            echo "$SCRIPT_CONTENT"
            HAS_ERROR=1
        fi

        # 标签配对
        for tag in script form; do
            OPENS=$(python3 -c "
content=open('$full').read()
import re
print(len(re.findall(r'<$tag[\\s>]', content)))" 2>/dev/null || echo "0")
            CLOSES=$(python3 -c "
content=open('$full').read()
print(content.count('</$tag>'))" 2>/dev/null || echo "0")
            if [ "$OPENS" != "$CLOSES" ]; then
                echo "  ✗ $f: <$tag> 标签不匹配 (开=$OPENS, 关=$CLOSES)"
                HAS_ERROR=1
            fi
        done
    done
fi

# ── Python 导入检查（仅限 staged 的 .py 文件）──
if [ -n "$CHANGED_PY" ]; then
    # 只检查 aelvoxim 包下的
    SERVER_PY=$(echo "$CHANGED_PY" | grep 'src/aelvoxim/' || true)
    if [ -n "$SERVER_PY" ]; then
        # 快速检查：尝试 import
        CHECK=$(python3 -c "
import sys
sys.path.insert(0, '$ROOT_DIR/src')
try:
    import aelvoxim
    print('ok')
except Exception as e:
    print(e)
" 2>/dev/null || echo "import failed")
        if [ "$CHECK" != "ok" ]; then
            echo "  ✗ Python 导入失败: $CHECK"
            HAS_ERROR=1
        fi
    fi
fi

if [ "$HAS_ERROR" -eq 1 ]; then
    echo ""
    echo "❌ pre-commit 检查未通过，提交已阻止"
    echo "   修复后重新 git add && git commit"
    echo "   或 git commit --no-verify 跳过检查"
    exit 1
fi

echo "  ✓ pre-commit 通过"
