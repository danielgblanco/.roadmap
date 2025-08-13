import os
import yaml
import base64
import argparse
from pathlib import Path
from github import Github
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

        self.roadmap_project_node_id = self._get_project_node_id(ROADMAP_PROJECT_ORG, ROADMAP_PROJECT_ID, owner_type="user")
        if not self.roadmap_project_node_id:
            raise Exception(f"Could not find roadmap project with ID {ROADMAP_PROJECT_ID} in org {ROADMAP_PROJECT_ORG}")

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

    def _get_sigs_yaml(self):
        """
        Gets the sigs.yml file from the community repository.
        """
        print("Getting sigs.yml file from community repository...")
        repo = self.github_client.get_repo(COMMUNITY_REPO)
        content = repo.get_contents(SIGS_FILE, ref="add_roadmap_management") # TODO: Remove to get from main branch
        return yaml.safe_load(base64.b64decode(content.content))

    def _get_project_node_id(self, owner, project_number, owner_type="organization"): # TODO: Remove owner_type parameter
        """
        Gets the node ID (e.g. PVT_kwDOAvross4AyFM8) for a project from its project number (e.g. 143).
        """
        print(f"Getting node ID for project {project_number} in {owner}...")
        if owner_type == "organization":
            query = gql(self.queries["get_project_node_id"])
            variables = {"org": owner, "project_number": project_number}
            response = self.graphql_client.execute(query, variable_values=variables)
            if response and response.get("organization") and response["organization"].get("projectV2"):
                return response["organization"]["projectV2"]["id"]
        elif owner_type == "user":
            query = gql(self.queries["get_user_project_node_id"])
            variables = {"login": owner, "project_number": project_number}
            response = self.graphql_client.execute(query, variable_values=variables)
            if response and response.get("user") and response["user"].get("projectV2"):
                return response["user"]["projectV2"]["id"]
        return None

    def _get_current_roadmap_items(self):
        """
        Fetches all items from the roadmap project. It returns a dictionary mapping project node IDs to their issues
        and item IDs. This is used to track the current state of the roadmap.
        """
        print("Fetching current roadmap items...")
        roadmap_items = {}
        query = gql(self.queries["get_roadmap_items"])
        has_next_page = True
        after_cursor = None

        while has_next_page:
            variables = {"roadmap_project_node_id": self.roadmap_project_node_id, "after": after_cursor}
            response = self.graphql_client.execute(query, variable_values=variables)

            if not response.get("node") or not response["node"].get("items"):
                break

            items_data = response["node"]["items"]
            for item in items_data.get("nodes", []):
                project_id_field = item.get("fieldValueByName")
                if project_id_field and project_id_field.get("text") and item.get("content"):
                    project_node_id = project_id_field["text"]
                    repo_name = item["content"]["repository"]["nameWithOwner"]
                    issue_number = item["content"]["number"]
                    try:
                        repo = self.github_client.get_repo(repo_name)
                        issue = repo.get_issue(issue_number)
                        roadmap_items[project_node_id] = {"issue": issue, "item_id": item["id"]}
                    except Exception as e:
                        print(f"Could not fetch issue {repo_name}#{issue_number}. It might be inaccessible. Error: {e}")

            page_info = items_data.get("pageInfo", {})
            has_next_page = page_info.get("hasNextPage", False)
            after_cursor = page_info.get("endCursor") if has_next_page else None

        print(f"Found {len(roadmap_items)} existing, tagged roadmap issues.")
        return roadmap_items

    def _get_project_details(self, project_node_id):
        """
        Gets the details for a given project node ID.
        """
        print(f"Getting details for project {project_node_id}...")
        query = gql(self.queries["get_project_details"])
        variables = {"project_node_id": project_node_id}
        response = self.graphql_client.execute(query, variable_values=variables)
        return response.get("node")

    def _get_project_fields(self, project_node_id):
        """
        Get the custom fields (e.g. status, start date, etc.) and their options for a project.
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

    def _create_or_update_issue(self, project_details, issue=None):
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
        issue_body = f"{short_description}\n\n---\n\n{readme}"

        if issue:
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

    def _add_issue_to_roadmap_project(self, issue, project_details, item_id=None):
        """
        Adds an issue to the roadmap project if it's not already there, and returns the item ID within the project.
        """
        if not item_id:
            if self.dry_run:
                print(f"[DRY RUN] Would add issue for project {project_details['title']} to roadmap project.")
                return None
            else:
                query = gql(self.queries["add_project_v2_item_by_id"])
                variables = {"project_id": self.roadmap_project_node_id, "content_id": issue.node_id}
                response_add = self.graphql_client.execute(query, variable_values=variables)
                item_id = response_add["addProjectV2ItemById"]["item"]["id"]
                print(f"Added issue for project {project_details['title']} to roadmap project.")
        return item_id

    def _update_roadmap_fields(self, item_id, project_details, sig_name):
        """
        Updates the custom fields in the roadmap project for a given item.
        """
        project_id_field = self.roadmap_fields.get("Project ID")
        status_field = self.roadmap_fields.get("Status")
        start_date_field = self.roadmap_fields.get("Start date")
        target_date_field = self.roadmap_fields.get("Target date")
        sig_field = self.roadmap_fields.get("SIG")

        latest_status_update = {}
        status_option_id = None
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

        variables = {
            "projectId": self.roadmap_project_node_id,
            "itemId": item_id,
            "projectIdFieldId": project_id_field["id"],
            "projectIdValue": project_details.get("id"),
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

    def _sync_project(self, project_node_id, current_roadmap_items, sig_name, issue=None):
        """
        Syncs a project to an issue in the roadmap repository.
        """
        print(f"Syncing project {project_node_id} to roadmap...")
        project_details = self._get_project_details(project_node_id)
        if not project_details:
            print(f"Could not get details for project {project_node_id}")
            return
        print(f"Found project '{project_details['title']}' for node ID {project_node_id}")

        issue = self._create_or_update_issue(project_details, issue)
        if not issue:
            print(f"Could not get create or update issue for project {project_details['title']}")
            return

        item_id = self._add_issue_to_roadmap_project(issue, project_details, current_roadmap_items.get(project_node_id, {}).get("item_id"))
        if not item_id:
            print(f"Could not get add issue {issue.id} to roadmap project")
            return

        self._update_roadmap_fields(item_id, project_details, sig_name)

    def _handle_removed_projects(self, active_project_node_ids, current_roadmap_items):
        """
        Removes items from the roadmap project if they are no longer in sigs.yml.
        """
        print("Checking for removed projects...")
        items_to_remove = []
        for project_node_id, data in current_roadmap_items.items():
            if project_node_id not in active_project_node_ids:
                items_to_remove.append((data["item_id"], data["issue"].html_url))

        if not items_to_remove:
            print("No projects to remove.")
            return

        delete_query = gql(self.queries["delete_project_v2_item"])
        for item_id, issue_url in items_to_remove:
            if self.dry_run:
                print(f"[DRY RUN] Would remove item {item_id} (for issue {issue_url}) from roadmap project.")
            else:
                delete_variables = {"project_id": self.roadmap_project_node_id, "item_id": item_id}
                self.graphql_client.execute(delete_query, variable_values=delete_variables)
                print(f"Removed item {item_id} (for issue {issue_url}) from roadmap project.")

    def _sync_projects_from_sigs(self, sigs_yaml, current_roadmap_items):
        """
        Syncs all projects from sigs.yml to the roadmap.
        """
        print("Syncing projects from sigs.yml...")
        active_project_node_ids = []
        for sig_group in sigs_yaml:
            for sig in sig_group.get("sigs", []):
                sig_name = sig.get("name")
                for project_number in sig.get("roadmapProjectIDs", []):
                    if not project_number:
                        continue

                    project_node_id = self._get_project_node_id(SOURCE_PROJECTS_ORG, project_number)
                    if not project_node_id:
                        print(f"Could not find project with ID {project_number} in org {SOURCE_PROJECTS_ORG}")
                        continue

                    active_project_node_ids.append(project_node_id)
                    existing_issue = current_roadmap_items.get(project_node_id, {}).get("issue")
                    self._sync_project(project_node_id, current_roadmap_items, sig_name, existing_issue)

        self._handle_removed_projects(active_project_node_ids, current_roadmap_items)

    def sync(self):
        """
        Main sync function.
        """
        sigs_yaml = self._get_sigs_yaml()
        current_roadmap_items = self._get_current_roadmap_items()
        self._sync_projects_from_sigs(sigs_yaml, current_roadmap_items)


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
        manager.sync()
    except Exception as e:
        print(f"An error occurred: {e}")


if __name__ == "__main__":
    main()
