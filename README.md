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
pku-disk mkdir -p '/Agent Uploads/run-001'
pku-disk put ./result.zip '/Agent Uploads/run-001' --json
pku-disk get '/model.pt' ./downloads/
```

All inspection commands support `--json`. Output data goes to stdout and errors
go to stderr, with exit code `0` for success and non-zero for failure.

Uploads refuse same-name conflicts by default. Use `--rename-on-conflict` to
keep both files. This initial release intentionally has no delete command.

## Install the Agent skill

Install the bundled Codex skill after installing the CLI:

```bash
pku-disk skill install
```

Restart Codex after installing the skill. Authentication remains an interactive
user step; an Agent should never request or handle the PKU password.

## Scope

The API paths used here were verified against PKU's AnyShare `7.0.6.3` web
deployment. This is an independent client, not an official PKU or AISHU tool.
