#!/bin/bash
# Release script for MetaCore
# Usage: bash scripts/release.sh [patch|minor|major]

set -e

VERSION_FILE="src/metacore/__init__.py"
CURRENT_VERSION=$(grep '__version__' "$VERSION_FILE" | sed "s/.*\"\(.*\)\".*/\1/")

echo "Current version: $CURRENT_VERSION"
echo ""

# Bump version
if [ "$1" = "patch" ]; then
    NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1"."$2"."$3+1}')
elif [ "$1" = "minor" ]; then
    NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1"."$2+1".0"}')
elif [ "$1" = "major" ]; then
    NEW_VERSION=$(echo "$CURRENT_VERSION" | awk -F. '{print $1+1".0.0"}')
else
    echo "Usage: $0 [patch|minor|major]"
    exit 1
fi

echo "Bumping to: $NEW_VERSION"

# Update version in __init__.py
sed -i "s/__version__ = \"$CURRENT_VERSION\"/__version__ = \"$NEW_VERSION\"/" "$VERSION_FILE"

# Update version in pyproject.toml
sed -i "s/version = \"$CURRENT_VERSION\"/version = \"$NEW_VERSION\"/" "pyproject.toml"

# Update CHANGELOG
echo "" >> CHANGELOG.md
echo "## $NEW_VERSION — $(date +%Y-%m-%d)" >> CHANGELOG.md
echo "" >> CHANGELOG.md
echo "### Changed" >> CHANGELOG.md
echo "" >> CHANGELOG.md

echo ""
echo "Version bumped. Run:"
echo "  git add -A && git commit -m 'v$NEW_VERSION' && git tag v$NEW_VERSION"
echo "  python -m build"
echo "  python -m twine upload dist/*"
