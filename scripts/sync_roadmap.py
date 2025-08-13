import os
import re
import yaml
import base64
import argparse
from pathlib import Path
from github import Github, Issue
from gql import gql, Client
from gql.transport.requests import RequestsHTTPTransport

COMMUNITY_REPO = "danielgblanco/community"
SIGS_FILE = "sigs.yml"
SOURCE_PROJECTS_ORG = "open-telemetry"

ROADMAP_PROJECT_ORG = "danielgblanco"
ROADMAP_PROJECT_ID = 7
ROADMAP_REPO = "danielgblanco/.roadmap"

STATUS_TO_FIELD_MAP = {
    "ON_TRACK": "On track",
    "AT_RISK": "At risk",
    "OFF_TRACK": "Off track",
    "COMPLETE": "Complete",
}

class RoadmapManager:
    def __init__(self, github_token, dry_run=False):
        self.dry_run = dry_run
        self.graphql_client = self._create_graphql_client(github_token)
        self.github_client = Github(github_token)
        self.queries = self._load_all_queries()

        project_details = self._get_project_details_by_number(ROADMAP_PROJECT_ORG, ROADMAP_PROJECT_ID, owner_type="user") #TODO: remove owner_type when moving to org
        if not project_details:
            raise Exception(f"Could not find roadmap project with ID {ROADMAP_PROJECT_ID} in org {ROADMAP_PROJECT_ORG}")
        self.roadmap_project_node_id = project_details["id"]

        self.roadmap_repo = self.github_client.get_repo(ROADMAP_REPO)
        self.roadmap_fields = self._get_project_fields(self.roadmap_project_node_id)

    @staticmethod
    def _load_all_queries():
        """
        Loads all GraphQL queries from the graphql directory.
        """
        queries = {}
        query_dir = Path(__file__).parent / "graphql"
        for query_path in query_dir.glob("*.graphql"):
            with open(query_path, "r") as f:
                queries[query_path.stem] = f.read()
        return queries

    @staticmethod
    def _create_graphql_client(github_token):
        transport = RequestsHTTPTransport(
            url="https://api.github.com/graphql",
            headers={"Authorization": f"Bearer {github_token}"},
            use_json=True,
        )
        return Client(transport=transport, fetch_schema_from_transport=True)

    def _get_project_details_by_number(self, owner: str, project_number: int, owner_type: str = "organization") -> dict | None:
        """
        Gets the project details for a project from an owner and project number.
        """
        print(f"Getting details for project {project_number} in {owner}...")
        if owner_type == "organization":
            query = gql(self.queries["get_project_details_by_number_org"])
            variables = {"org": owner, "project_number": project_number}
            response = self.graphql_client.execute(query, variable_values=variables)
            if response and response.get("organization") and response["organization"].get("projectV2"):
                project_details = response["organization"]["projectV2"]
                project_details["project_number"] = project_number
                return project_details
        elif owner_type == "user":
            query = gql(self.queries["get_project_details_by_number_user"])
            variables = {"login": owner, "project_number": project_number}
            response = self.graphql_client.execute(query, variable_values=variables)
            if response and response.get("user") and response["user"].get("projectV2"):
                project_details = response["user"]["projectV2"]
                project_details["project_number"] = project_number
                return project_details
        return None

    def _get_project_item_for_issue(self, issue: Issue) -> dict | None:
        """
        Given an issue, fetch the associated item in the roadmap project (if any).
        Returns the raw item object, or None if not found.
        Uses a direct GraphQL query by content (issue) node ID for efficiency.
        """
        query = gql(self.queries["get_project_item_by_issue_id"])
        variables = {
            "project_id": self.roadmap_project_node_id,
            "content_id": issue.node_id,
        }
        response = self.graphql_client.execute(query, variable_values=variables)
        return response.get("node", {}).get("projectV2ItemByContent")

    def _get_project_fields(self, project_node_id: str) -> dict:
        """
        Get the custom fields (e.g. status, start date, etc.) and their options for a project.
        Note: GraphQL query returns a maximum of 100 fields, which is sufficient for our use case.
        """
        query = gql(self.queries["get_project_fields"])
        variables = {"project_node_id": project_node_id}
        response = self.graphql_client.execute(query, variable_values=variables)
        fields = {}
        if response.get("node", {}).get("fields", {}).get("nodes"):
            for field in response["node"]["fields"]["nodes"]:
                field_data = {"id": field["id"], "type": field["dataType"]}
                if "options" in field:
                    field_data["options"] = {option["name"]: option["id"] for option in field["options"]}
                fields[field["name"]] = field_data
        return fields

    def _create_or_update_issue(self, project_details: dict, issue: Issue = None) -> Issue | None:
        """
        Creates or updates an issue for a given project.
        """
        issue_title = f"{project_details['title']}"

        short_description = project_details.get('shortDescription')
        if not short_description or short_description == "None":
            short_description = "No short description provided."
        readme = project_details.get('readme')
        if not readme or readme == "None":
            readme = "No README provided."

        # Add a hidden marker to the body to store the source project ID
        project_id_comment = f"<!-- source-project-id: {project_details['id']} -->"
        # Add a link to the source project if available
        source_project_section = f"## https://github.com/orgs/{SOURCE_PROJECTS_ORG}/projects/{project_details['project_number']}"

        # Construct the issue body
        issue_body = f"{source_project_section}\n\n{short_description}\n\n## README\n\n{readme}\n\n{project_id_comment}"

        if issue:
            # Check if the main content and title have changed
            if issue.title == issue_title and issue.body == issue_body:
                print(f"No changes to issue for project {project_details['title']}")
                return issue

            if self.dry_run:
                print(f"[DRY RUN] Would update issue for project {project_details['title']} in {self.roadmap_repo.full_name}")
            else:
                issue.edit(title=issue_title, body=issue_body)
                print(f"Updated issue for project {project_details['title']}")
        else:
            if self.dry_run:
                print(f"[DRY RUN] Would create issue for project {project_details['title']} in {self.roadmap_repo.full_name}")
                return None
            else:
                issue = self.roadmap_repo.create_issue(title=issue_title, body=issue_body)
                print(f"Created issue for project {project_details['title']}")
        return issue

    def _add_issue_to_roadmap_project(self, issue: Issue, project_details: dict) -> dict | None:
        """
        Adds an issue to the roadmap project if it's not already there, and returns the project item details.
        """
        if self.dry_run:
            print(f"[DRY RUN] Would add issue for project {project_details['title']} to roadmap project.")
            return None
        else:
            query = gql(self.queries["add_project_item_by_issue_id"])
            variables = {"project_id": self.roadmap_project_node_id, "content_id": issue.node_id}
            response_add = self.graphql_client.execute(query, variable_values=variables)
            print(f"Added issue for project {project_details['title']} to roadmap project.")
            return response_add["addProjectV2ItemById"]["item"]

    def _update_roadmap_fields(self, project_item_details: dict, project_details: dict, sig_name: str) -> None:
        """
        Updates the custom fields in the roadmap project for a given item.
        """
        item_id = project_item_details["id"]
        status_field = self.roadmap_fields.get("Status")
        start_date_field = self.roadmap_fields.get("Start date")
        target_date_field = self.roadmap_fields.get("Target date")
        sig_field = self.roadmap_fields.get("SIG")

        latest_status_update = {}
        status_option_id = None
        human_readable_status = None
        latest_status_update_nodes = project_details.get("latestStatusUpdate", {}).get("nodes", [{}])
        if len(latest_status_update_nodes) > 0:
            latest_status_update = latest_status_update_nodes[0]
            # Get the status option ID for the latest status update (e.g. ON_TRACK, AT_RISK, etc.)
            if latest_status_update.get("status"):
                api_status = latest_status_update["status"]
                human_readable_status = STATUS_TO_FIELD_MAP.get(api_status)
                if human_readable_status:
                    status_option_id = status_field.get("options", {}).get(human_readable_status)
                    if not status_option_id:
                        print(f"Warning: Status '{human_readable_status}' not found in roadmap project options.")
                else:
                    print(f"Warning: Unknown status '{api_status}' received from API for project {project_details.get('id')}.")

        # Extract current values from the project item details
        current_status = project_item_details.get("status", {}).get("name") if project_item_details.get("status") else None
        current_start_date = project_item_details.get("startDate", {}).get("date") if project_item_details.get("startDate") else None
        current_target_date = project_item_details.get("targetDate", {}).get("date") if project_item_details.get("targetDate") else None
        current_sig = project_item_details.get("sig", {}).get("text") if project_item_details.get("sig") else None

        if all([
            current_status == human_readable_status,
            current_start_date == latest_status_update.get("startDate"),
            current_target_date == latest_status_update.get("targetDate"),
            current_sig == sig_name,
        ]):
            print(f"No changes to roadmap fields for item {item_id}")
            return

        variables = {
            "projectId": self.roadmap_project_node_id,
            "itemId": item_id,
            "statusFieldId": status_field["id"],
            "statusValue": status_option_id,
            "startDateFieldId": start_date_field["id"],
            "startDateValue": latest_status_update.get("startDate"),
            "targetDateFieldId": target_date_field["id"],
            "targetDateValue": latest_status_update.get("targetDate"),
            "sigFieldId": sig_field["id"],
            "sigValue": sig_name,
        }

        if self.dry_run:
            print(f"[DRY RUN] Would update fields for item {item_id} with values: {variables}")
            return

        query = gql(self.queries["update_project_item_fields"])
        self.graphql_client.execute(query, variable_values=variables)
        print(f"Updated fields for item {item_id}.")

    def _sync_project(self, project_details: dict, sig_name: str, issue: Issue = None) -> None:
        """
        Syncs a project to an issue in the roadmap repository.
        """
        print(f"Syncing project '{project_details['title']}' to roadmap...")

        issue = self._create_or_update_issue(project_details, issue)
        if not issue:
            print(f"Could not get create or update issue for project {project_details['title']}")
            return

        # Fetch the project item and its fields for this issue (these are details stored in the roadmap project,
        # not in the project itself so we need to get them separately)
        project_item_details = self._get_project_item_for_issue(issue)

        # If project item details are not found, we need to add the issue to the roadmap project
        if not project_item_details:
            project_item_details = self._add_issue_to_roadmap_project(issue, project_details)
            if not project_item_details:
                print(f"Could not add issue {issue.id} to roadmap project")
                return

        self._update_roadmap_fields(project_item_details, project_details, sig_name)

    def get_current_roadmap_issues(self) -> dict:
        """
        Fetches all issues from the roadmap repository that are linked to a source project.
        Returns a dictionary mapping project node IDs to their issues.
        """
        print("Fetching current roadmap issues...")
        roadmap_items = {}
        for issue in self.roadmap_repo.get_issues(state="open"):
            if issue.body:
                match = re.search(r"<!-- source-project-id: (.*) -->", issue.body)
                if match:
                    project_node_id = match.group(1)
                    roadmap_items[project_node_id] = {"issue": issue}
        print(f"Found {len(roadmap_items)} issues linked to a source project.")
        return roadmap_items

    def get_roadmap_project_items(self) -> list:
        """
        Fetches all items from the roadmap project.
        """
        print("Fetching roadmap project items...")
        query = gql(self.queries["get_roadmap_items"])
        variables = {"project_node_id": self.roadmap_project_node_id}
        response = self.graphql_client.execute(query, variable_values=variables)
        if response.get("node") and response["node"].get("items"):
            return response["node"]["items"]["nodes"]
        return []

    def get_sigs_projects(self) -> dict[str, list[dict]]:
        """
        Gets the sigs.yml file from the community repository and returns a dictionary of SIGs and their project details.
        """
        print("Getting sigs.yml file from community repository...")
        repo = self.github_client.get_repo(COMMUNITY_REPO)
        content = repo.get_contents(SIGS_FILE, ref="add_roadmap_management")  # TODO: Remove to get from main branch
        sigs_yaml = yaml.safe_load(base64.b64decode(content.content))

        sigs_projects = {}
        for sig_group in sigs_yaml:
            for sig in sig_group.get("sigs", []):
                sig_name = sig.get("name")
                project_details_list = []
                for project_number in sig.get("roadmapProjectIDs", []):
                    if not project_number:
                        continue

                    project_details = self._get_project_details_by_number(SOURCE_PROJECTS_ORG, project_number)
                    if not project_details:
                        print(f"Could not find project with ID {project_number} in org {SOURCE_PROJECTS_ORG}")
                        continue
                    project_details_list.append(project_details)

                if sig_name and project_details_list:
                    sigs_projects[sig_name] = project_details_list
        return sigs_projects

    def sync_projects_from_sigs(self, sigs_projects: dict[str, list[dict]], current_roadmap_issues: dict) -> None:
        """
        Syncs all projects from sigs.yml to the roadmap.
        """
        print("Syncing projects from sigs.yml...")
        for sig_name, project_details_list in sigs_projects.items():
            for project_details in project_details_list:
                project_node_id = project_details["id"]
                existing_issue = current_roadmap_issues.get(project_node_id, {}).get("issue")
                self._sync_project(project_details, sig_name, existing_issue)

    def handle_removed_projects(self, sigs_projects: dict[str, list[dict]]) -> None:
        """
        Removes items from the roadmap project if they are no longer in sigs.yml.
        Please note that this will not delete or close the issues themselves, only the project items on the board.
        """
        print("Checking for removed projects...")
        active_project_node_ids = set()
        for project_details_list in sigs_projects.values():
            for project_details in project_details_list:
                active_project_node_ids.add(project_details["id"])

        roadmap_items = self.get_roadmap_project_items()
        items_to_remove = []
        for item in roadmap_items:
            if item.get("content") and item["content"].get("body"):
                match = re.search(r"<!-- source-project-id: (.*) -->", item["content"]["body"])
                if match:
                    project_node_id = match.group(1)
                    if project_node_id not in active_project_node_ids:
                        items_to_remove.append((item["id"], item["content"]["url"]))

        if not items_to_remove:
            print("No projects to remove.")
            return

        delete_query = gql(self.queries["delete_project_item_by_item_id"])
        for item_id, issue_url in items_to_remove:
            if self.dry_run:
                print(f"[DRY RUN] Would remove item {item_id} (for issue {issue_url}) from roadmap project.")
            else:
                delete_variables = {"project_id": self.roadmap_project_node_id, "item_id": item_id}
                self.graphql_client.execute(delete_query, variable_values=delete_variables)
                print(f"Removed item {item_id} (for issue {issue_url}) from roadmap project.")

def main():
    """
    Main function.
    """
    parser = argparse.ArgumentParser(description="Sync OpenTelemetry roadmap projects to issues.")
    parser.add_argument("--dry-run", action="store_true", help="Run the script without making any changes.")
    args = parser.parse_args()

    github_token = os.environ.get("GH_TOKEN")
    if not github_token:
        print("GH_TOKEN environment variable is not set")
        return

    try:
        manager = RoadmapManager(github_token, args.dry_run)
        sigs_projects = manager.get_sigs_projects()
        current_roadmap_issues = manager.get_current_roadmap_issues()
        manager.sync_projects_from_sigs(sigs_projects, current_roadmap_issues)
        manager.handle_removed_projects(sigs_projects)
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
