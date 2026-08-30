---
name: pku-disk
description: Use the pku-disk CLI only when the user explicitly names 北大网盘, PKU Disk, disk.pku.edu.cn, PKU AnyShare, or clearly asks to transfer something to or from that specific service. Do not invoke for generic uploads, downloads, cloud storage, local files, or other network disks.
---

# PKU Disk

Load this skill only for an explicit PKU disk request. A generic request to
upload, download, sync, or manage files is not sufficient.

Use `pku-disk` rather than browser automation for file operations on the user's
PKU AnyShare storage.

Before the first operation, run `pku-disk auth status`. If authentication has
expired, ask the user to run `pku-disk auth login` and complete PKU IAAA login in
the opened browser. Never ask for, type, store, or transmit the user's PKU
password.

Prefer machine-readable output for inspection:

```bash
pku-disk ls / --json
pku-disk tree '/remote/folder' --json
pku-disk stat '/remote/file' --json
pku-disk share policy --json
pku-disk share list --json
pku-disk share link list '/remote/item' --json
pku-disk share grants '/remote/item' --json
```

For transfers:

```bash
pku-disk get '/remote/file.zip' ./destination/
pku-disk put ./artifact.zip '/remote/folder' --json
```

Remote paths are absolute and case-sensitive. Quote paths containing spaces or
non-ASCII characters. Uploads reject same-name conflicts unless the user
explicitly requests `--rename-on-conflict`.

For anonymous links:

```bash
pku-disk share link create '/remote/item' --permission preview --permission download --json
pku-disk share link update LINK_ID --expires-days 7 --json
pku-disk share link revoke LINK_ID --yes
```

Add `--password` to prompt securely, or supply
`PKU_DISK_SHARE_PASSWORD` through the process environment. Never put a share
password directly in command arguments or reveal it in conversation.

For internal sharing, search first and use the returned stable ID:

```bash
pku-disk share accessors 'person or department' --json
pku-disk share grant '/remote/item' --accessor-id ID --accessor-type user --permission preview --permission download --json
pku-disk share revoke '/remote/item' --accessor-id ID --accessor-type user --yes
```

Never guess an accessor from an ambiguous search result. Ask the user to choose
when multiple people, departments, or groups plausibly match.

Listing, metadata inspection, and downloading are read-only. Creating folders
and uploading files change cloud state: perform them only when the user's
request authorizes that specific destination and content. Creating, updating,
or revoking a link and adding or revoking an internal grant change external
access: do them only when explicitly authorized for that exact item and
recipient. Inspect policy and existing grants first. Revocation requires
`--yes`. The CLI intentionally does not expose file deletion.
