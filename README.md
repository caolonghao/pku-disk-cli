# pku-disk-cli

Agent-friendly command-line access to Peking University's AnyShare deployment.
It uses the same REST endpoints as the official web client and authenticates
through PKU IAAA in a temporary browser session. Your PKU password is never
handled or stored by this CLI.

## Install

Python 3.10 or newer and Google Chrome are recommended.

```bash
pipx install 'pku-disk-cli[browser] @ git+https://github.com/caolonghao/pku-disk-cli.git'
pku-disk skill install
```

Alternatively, install the core CLI and import a short-lived token manually:

```bash
pipx install git+https://github.com/caolonghao/pku-disk-cli.git
pku-disk auth import-token
```

## Authenticate

```bash
pku-disk auth login
pku-disk auth status
```

`auth login` launches a clean Chrome window. Complete PKU IAAA authentication
yourself. The resulting AnyShare Bearer token is saved in macOS Keychain. On
other systems it is stored in a mode-0600 config file. Tokens are short-lived;
run the command again when the server reports expiration.

## Use

```bash
pku-disk ls /
pku-disk tree '/LLM Test'
pku-disk stat '/model.pt' --json
pku-disk quota --json
pku-disk search 'report' --type file --json
pku-disk mkdir -p '/Agent Uploads/run-001'
pku-disk put ./result.zip '/Agent Uploads/run-001' --json
pku-disk put ./results '/Agent Uploads/run-001' --json
pku-disk get '/model.pt' ./downloads/
pku-disk get '/Agent Uploads/run-001/results' ./downloads/
```

All inspection commands support `--json`. Output data goes to stdout and errors
go to stderr, with exit code `0` for success and non-zero for failure.

File and directory transfers are recursive. Uploads larger than 16 MiB use
4 MiB multipart chunks and automatically resume completed chunks after an
interruption. Uploads refuse same-name conflicts by default; use
`--rename-on-conflict` to keep both items.

## Manage files and the recycle bin

```bash
pku-disk rename '/Agent Uploads/old.txt' 'new.txt'
pku-disk mv '/Agent Uploads/new.txt' '/Archive'
pku-disk cp '/Archive/new.txt' '/Agent Uploads'
pku-disk rm '/Agent Uploads/new.txt' --yes
pku-disk trash list --json
pku-disk trash restore 'gns://EXACT_ITEM_ID' --yes
pku-disk trash delete 'gns://EXACT_ITEM_ID' --yes
```

`rm` moves an item to AnyShare's recycle bin. Permanent deletion requires the
stable item ID returned by a fresh `trash list` and cannot be undone. All three
recycle-bin mutations require `--yes`.

## Share files and folders

Inspect the deployment's sharing policy and existing shares:

```bash
pku-disk share policy --json
pku-disk share list --json
pku-disk share link list '/Agent Uploads' --json
pku-disk share grants '/Agent Uploads' --json
```

Create and manage anonymous links:

```bash
pku-disk share link create '/Agent Uploads' \
  --permission preview --permission download --expires-days 30 --json
pku-disk share link create '/report.pdf' --password --max-uses 10 --json
pku-disk share link update LINK_ID --expires-days 7 --json
pku-disk share link revoke LINK_ID --yes
```

`--password` reads from a hidden prompt, or from
`PKU_DISK_SHARE_PASSWORD` for non-interactive automation. Passwords are never
included in CLI output. `--expires-days 0` means no expiration and
`--max-uses -1` means unlimited use.

Share with a PKU user, department, or group by first resolving its stable ID:

```bash
pku-disk share accessors '张三' --json
pku-disk share grant '/Agent Uploads' \
  --accessor-id USER_ID --accessor-type user \
  --permission preview --permission download --json
pku-disk share revoke '/Agent Uploads' \
  --accessor-id USER_ID --accessor-type user --yes
```

The user-facing permissions are `preview`, `download`, and `upload`; `upload`
maps to AnyShare's create and modify permissions. Grant and revoke operations
preserve unrelated access-control entries. Revocation requires `--yes`.

## Install the Agent skill

Install the bundled Codex skill after installing the CLI:

```bash
pku-disk skill install
```

Restart Codex after installing the skill. Authentication remains an interactive
user step; an Agent should never request or handle the PKU password.

## Scope

The API paths used here were verified against PKU's AnyShare `7.0.6.3` web
deployment. Directory copies are executed deterministically as recursive
server-side file copies because the deployment's asynchronous directory-copy
queue can remain waiting indefinitely. A failed recursive copy may leave a
partial destination directory that can be inspected and removed explicitly.
This is an independent client, not an official PKU or AISHU tool.
