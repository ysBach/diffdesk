# diffdesk: guide for coding agents

diffdesk is a local review desk for commits. For each change, you make a commit
and write one subject file: the commit range and your reasons. A person reads
the reasons and the diff in the browser. The person accepts the change, rejects
it, or asks for a change. The person can also write a comment and edit the new
version of a file. You read the verdicts and the edits back with one command.

## Steps

1. Make one commit for each change. Do not put unrelated changes in one commit.

2. Write a subject for the commit:

       diffdesk add REVIEW_ROOT --repo REPO --id FIX-12 \
           --title "Keep the last page in the pager" \
           --summary "The loop stopped one page early; it now includes the last page." \
           --why-file why.md

   - By default the diff is `HEAD^..HEAD`. Use `--commit REV` for another commit, or
     `--base REV --head REV` for a range.
   - diffdesk gets the full commit IDs, the repository path and the changed
     files from git, and checks the subject against the schema.
   - `--why-file -` reads the reasons from standard input. `--why TEXT` gives
     them on the command line.
   - Other options: `--package`, `--priority`, `--recommended`, `--tag`
     (repeatable), `--branch`, `--json` (print the subject).
   - diffdesk does not replace an existing subject. If you rewrite the commit
     (rebase or amend), run the same command with `--force` to point the
     subject at the new commit.

3. Ask the person to review: `diffdesk serve REVIEW_ROOT --open`.

4. Read the requests:

       diffdesk reviews REVIEW_ROOT --status change --status rejected

5. For each request, read the comment and the edits. Make a new commit and add
   a new subject for it. Name the old subject ID in `why`.

## Write a good `why`

`why` is Markdown. LaTeX in `$...$` is allowed. Use these headings:

- **Problem**: what is wrong, and where (file, function, input).
- **Evidence**: how you know. For example a failing test, an error message,
  or a measurement.
- **Fix logic**: why this change removes the problem.
- **Test**: the command you ran and its result.
- **Tradeoffs**: what the change costs, what it does not fix, and the other
  options that you did not use.

## Output of `diffdesk reviews`

A JSON list with one object for each subject:

| Field | Meaning |
| --- | --- |
| `id`, `package`, `title` | The subject. |
| `status` | `pending`, `accepted`, `rejected` or `change` (the last verdict). |
| `verdict`, `comment`, `at` | The last review record: `accept`, `reject` or `change`, the comment, and the time. `null` if there is no record. |
| `history_count` | Number of review records for the subject. |
| `repo`, `head` | Work tree and new-side commit of the subject. |
| `edits` | `[{path, edit_file}]`: the file path in the repository and the absolute path of the saved edit. |

Filters: `--status` (repeatable), `--package`, `--id`. `--format text` prints a
short table for people.

## Edits

An edit file holds the full new content of one file, as the person changed it.
It is not a patch. To see what the person changed, compare it with the version
of that file at the subject's `head`:

    git -C REPO show SHA:PATH > head-version
    git diff --no-index head-version EDIT_FILE

Here `SHA` is the `head` value of the subject, `PATH` is `path`, and
`EDIT_FILE` is `edit_file`.

## Review root

    REVIEW_ROOT/
      diffdesk.toml                  optional settings
      subjects/<package>/<ID>.json   subjects (written by diffdesk add)
      reviews.json                   verdicts, oldest first (written by the browser)
      edits/<package>/<ID>/<path>    edited files (written by the browser)
      .diffdesk-cache/               cache, safe to delete

## Subject fields

Only `id` and `title` are required. `diffdesk validate REVIEW_ROOT` checks
every subject file.

| Field | Meaning |
| --- | --- |
| `id` | Letters, digits, `.`, `_`, `-`. Starts with a letter or digit. No `..`. Unique inside its package. |
| `title` | One-line title. |
| `package` | Same rules as `id`. Default: the folder below `subjects/`. |
| `priority`, `origin`, `recommended` | For example `P1`; `new`; `accept`, `reject` or `user-decision`. |
| `summary` | One sentence: what is wrong and what the change does. |
| `why` | Markdown with the reasons. |
| `repo` | Absolute path of the git work tree. |
| `base`, `head` | Old and new side of the diff (commit IDs). |
| `files` | Paths to review, relative to `repo`. Empty: every changed file. |
| `tests` | Test command and result. |
| `decisions`, `refs`, `tags`, `depends_on` | Lists of strings: decision IDs, documents, labels, subject IDs. |
| `branch`, `order`, `senior_verdict` | Branch for the tree, position in the branch, verdict of a second reviewer. |

Other fields are kept and shown in a "More" table.
