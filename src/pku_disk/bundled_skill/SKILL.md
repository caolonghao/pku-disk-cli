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
```

For transfers:

```bash
pku-disk get '/remote/file.zip' ./destination/
pku-disk put ./artifact.zip '/remote/folder' --json
```

Remote paths are absolute and case-sensitive. Quote paths containing spaces or
non-ASCII characters. Uploads reject same-name conflicts unless the user
explicitly requests `--rename-on-conflict`.

Listing, metadata inspection, and downloading are read-only. Creating folders
and uploading files change cloud state: perform them only when the user's
request authorizes that specific destination and content. The CLI intentionally
does not expose deletion.
