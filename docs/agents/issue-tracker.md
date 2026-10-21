# Issue tracker: GitHub

Issues and specs live in GitHub Issues. Use the `gh` CLI.

## Repository

The GitHub repository is `Leegerger/auto_plan`: https://github.com/Leegerger/auto_plan.

Before performing tracker operations, resolve the repository from `git remote -v`. If no GitHub remote exists, ask for the target repository. Use `--repo OWNER/REPO` when it cannot be inferred from the checkout.

## Conventions

- Create: `gh issue create --title "..." --body-file <file>`
- Read: `gh issue view <number> --comments`
- List: `gh issue list --state open --json number,title,body,labels`
- Comment: `gh issue comment <number> --body-file <file>`
- Apply or remove labels: `gh issue edit <number> --add-label "..."` or `--remove-label "..."`
- Close: `gh issue close <number> --comment "..."`

For multiline bodies, save the text to a file and use `--body-file`.

When a skill says “publish to the issue tracker,” create a GitHub issue.
When a skill says “fetch the relevant ticket,” read the issue and its comments.

## Pull requests as a triage surface

**PRs as a request surface: no.**

## Wayfinding operations

Use a GitHub issue labelled `wayfinder:map` as the canonical map. Decision tickets are native sub-issues, each with one `wayfinder:grilling`, `wayfinder:prototype`, `wayfinder:research`, or `wayfinder:task` label. These describe ticket types; triage roles continue to use the labels in `triage-labels.md`.

Use `OWNER/REPO` below for the repository verified from the remote. Create issue bodies with `--body-file`. Refer to maps and tickets by their linked titles in human-facing text.

1. Find maps with `gh issue list --repo OWNER/REPO --state open --label wayfinder:map`.
2. Create the map and its decision issues before wiring relationships. Keep newly charted tickets open and unassigned.
3. Obtain an issue's numeric database ID with `gh api repos/OWNER/REPO/issues/NUMBER --jq .id`. Relationship bodies require this ID, not the issue number or GraphQL node ID.
4. Attach each child with `gh api --method POST repos/OWNER/REPO/issues/MAP_NUMBER/sub_issues -F sub_issue_id=CHILD_DATABASE_ID`. Preserve any existing parent unless changing it is intended.
5. Add a native blocking edge with `gh api --method POST repos/OWNER/REPO/issues/DEPENDENT_NUMBER/dependencies/blocked_by -F issue_id=BLOCKER_DATABASE_ID`. A permission or network failure does not justify replacing native dependencies with body text.
6. Verify each parent-child relationship and blocking edge after writing. Inspect existing state before retrying a failed create or relationship request.

### Frontier and claims

- Read children with `gh api repos/OWNER/REPO/issues/MAP_NUMBER/sub_issues --paginate`.
- For each open, unassigned child, read blockers with `gh api repos/OWNER/REPO/issues/NUMBER/dependencies/blocked_by --paginate`.
- The frontier consists of those children whose blockers are all closed, including children with no blockers. Select the first in the map's sub-issue order unless the user names one.
- Claim a selected ticket before working with `gh issue edit NUMBER --repo OWNER/REPO --add-assignee @me`; read it back to verify the assignment.

### Resolution and index

Post the resolution as a comment, close the child issue, then update the map's Decisions so far with a linked title and one-line gist. Re-read the map immediately before editing to preserve concurrent updates. Keep decision detail in the resolution comment; open work is discoverable through native sub-issues and dependencies.

GitHub API references: [sub-issues](https://docs.github.com/en/rest/issues/sub-issues) and [issue dependencies](https://docs.github.com/en/rest/issues/issue-dependencies).
