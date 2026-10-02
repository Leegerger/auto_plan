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
