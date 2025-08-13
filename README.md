# OpenTelemetry Roadmap

This repository manages the OpenTelemetry roadmap, which is publicly available at **[OpenTelemetry Roadmap](https://github.com/orgs/open-telemetry/projects/155)**.

The roadmap is generated automatically from a selected subset of GitHub projects in order to overcome limitations in GitHub's native project functionality.

## How it works

A GitHub Action in this repository runs periodically to:

1.  Read the list of roadmap projects from the `sigs.yml` file in the `open-telemetry/community` repository.
2.  For each project, it creates or updates an issue in this repository. The issue contains details from the project, such as the project name, README, and status updates.
3.  The issue is then added to the [OpenTelemetry Roadmap project](https://github.com/users/danielgblanco/projects/7), and its status is synced with the project's status.

This mechanism allows for a centralized roadmap view while letting individual project teams manage their work and status independently in their own projects.

## Roadmap Item Selection

For a project to appear on the official roadmap, its ID must be added to `sigs.yml` in the list of `roadmapProjectIDs` under its corresponding SIG. This is an opt-in process, coordinated by GC and TC.

