---
name: pku-disk
description: Use the pku-disk CLI only when the user explicitly names 北大网盘, PKU Disk, disk.pku.edu.cn, PKU AnyShare, or clearly asks to transfer something to or from that specific service. Do not invoke for generic uploads, downloads, cloud storage, local files, or other network disks.
---

# PKU Disk

Load this skill only for an explicit PKU disk request. A generic request to
upload, download, sync, or manage files is not sufficient.

Use `pku-disk` rather than browser automation for file operations on the user's
PKU AnyShare storage.

Before the first operation, run `pku-disk auth status`. The CLI automatically
refreshes an expired AnyShare token from its dedicated saved PKU SSO browser
session and may briefly open a Chrome window. If that session has also expired,
ask the user to run `pku-disk auth login` and complete PKU IAAA login in the
opened browser. Never
ask for, type, store, or transmit the user's PKU password. Never run
`auth forget-session` unless the user explicitly asks to remove saved login
state.

Prefer machine-readable output for inspection:

```bash
pku-disk ls / --json
pku-disk tree '/remote/folder' --json
pku-disk stat '/remote/file' --json
pku-disk quota --json
pku-disk search 'keyword' --type all --json
pku-disk trash list --json
pku-disk share policy --json
pku-disk share list --json
pku-disk share link list '/remote/item' --json
pku-disk share grants '/remote/item' --json
```

For transfers:

```bash
pku-disk get '/remote/file-or-folder' ./destination/
pku-disk put ./local-file-or-folder '/remote/folder' --json
```

Remote paths are absolute and case-sensitive. Quote paths containing spaces or
non-ASCII characters. Uploads reject same-name conflicts unless the user
explicitly requests `--rename-on-conflict`. Large uploads are multipart and
automatically resume completed parts after an interruption.

For file management and the recycle bin:

```bash
pku-disk rename '/remote/old' 'new'
pku-disk mv '/remote/item' '/remote/destination'
pku-disk cp '/remote/item' '/remote/destination'
pku-disk rm '/remote/item' --yes
pku-disk trash list --json
pku-disk trash restore 'gns://EXACT_ITEM_ID' --yes
pku-disk trash delete 'gns://EXACT_ITEM_ID' --yes
```

Never guess a recycle-bin item ID. Obtain it from a fresh `trash list` and
match the name and original path before restoring or deleting. `trash delete`
is permanent and requires explicit authorization for that exact item.

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
and uploading, renaming, moving, copying, removing, or restoring items change
cloud state: perform them only when the user's request authorizes that exact
item, destination, and content. Creating, updating, or revoking a link and
adding or revoking an internal grant change external access: do them only when
explicitly authorized for that exact item and recipient. Inspect policy and
existing grants first. Destructive commands require `--yes`.
