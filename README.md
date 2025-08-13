# OpenTelemetry Roadmap

This repository contains the automation for generating and maintaining the official [OpenTelemetry Roadmap project board](https://github.com/orgs/open-telemetry/projects/155).

The primary goal of this automation is to provide a centralized, high-level view of the key initiatives across the OpenTelemetry project, overcoming the limitations of GitHub's native project features which do not allow for aggregating projects into a single view.

### How it Works

The automation is driven by a Python script (`scripts/sync_roadmap.py`) that runs periodically via a GitHub Action.
Here is a step-by-step breakdown of the process:

1.  **Load Configuration**: The script reads the list of designated roadmap projects from the `sigs.yml` file located in the `open-telemetry/community` repository.
2.  **Fetch Project Details**: For each project ID listed in `sigs.yml`, the script fetches detailed information from the source project, including its title, description, README, and the latest status update.
3.  **Create or Update Issues**:
    *   The script creates a corresponding issue in this repository for each source project. A hidden HTML comment `<!-- source-project-id: ... -->` is added to the issue body to link it back to the source project.
    *   If an issue for a project already exists, the script updates its title and body to reflect any changes from the source project.
4.  **Sync with Roadmap Project Board**:
    *   The issue is added as an item to the central [OpenTelemetry Roadmap](https://github.com/users/danielgblanco/projects/7) project.
    *   The custom fields on the project item (like `Status`, `Start date`, `Target date`, and `SIG`) are updated to match the details from the source project.
5.  **Handle Removed Projects**: If a project ID is removed from `sigs.yml`, the script will find the corresponding item on the roadmap project board and remove it. This action does **not** close or delete the associated issue, it only removes the item from the board.

### Running the Script Manually

While the script is designed to be automated, it can be run locally for development or testing purposes.

#### Prerequisites

*   Python 3.10+
*   pip

#### Installation

1.  Clone the repository:
    ```bash
    git clone https://github.com/danielgblanco/.roadmap.git
    cd .roadmap
    ```
2.  Install the required Python packages:
    ```bash
    pip install -r requirements.txt
    ```

#### Configuration

The script is configured via constants defined at the top of `scripts/sync_roadmap.py`.
These should not need to be changed unless the source or destination repositories change.

#### Usage

The script requires a GitHub Personal Access Token (PAT) with the necessary permissions to be available as an environment variable.

1.  **Set the Environment Variable**:
    ```bash
    export GH_TOKEN="your_github_pat"
    ```
2.  **Run the Script**:
    ```bash
    python scripts/sync_roadmap.py
    ```
3.  **Dry Run**: To see what changes the script would make without actually performing any writes (e.g., creating/updating issues, modifying project items), use the `--dry-run` flag:
    ```bash
    python scripts/sync_roadmap.py --dry-run
    ```

### GitHub Actions Integration

This script is intended to be run as a scheduled GitHub Action. The workflow file (e.g., `.github/workflows/sync_roadmap.yml`) should be configured to run the script periodically (e.g., every 6 hours).

The `GH_TOKEN` must be stored as a secret in the repository settings and passed to the script in the workflow file.

### Required Permissions for `GH_TOKEN`

The GitHub token used to run the script needs the following permissions:

*   **`repo`**: Full control of private repositories.
    *   Required to read `sigs.yml` from `open-telemetry/community`.
    *   Required to create, read, and update issues in this (`.roadmap`) repository.
*   **`project`**: Read and write access to projects.
    *   Required to read source projects from the `open-telemetry` organization.
    *   Required to add, update, and remove items from the roadmap project board.
*   **`read:org`**: Read org and team membership.
    *   Required to access organization-level project details.

## Roadmap Item Selection

For a project to appear on the official roadmap, its ID must be added to `sigs.yml` in the list of `roadmapProjectIDs` under its corresponding SIG. This is an opt-in process, coordinated by the OpenTelemetry Governance Committee and Technical Committee.
