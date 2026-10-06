from typing import List, Dict, Any

class RelationshipTreeService:
    """
    Service responsible for generating the hierarchical relationship data 
    between Doctrines, Roles, and Characters.
    """

    def __init__(self, doctrine_manager, role_manager):
        """
        Initializes the service with necessary managers.

        :param doctrine_manager: Manager for doctrine-related operations.
        :param role_manager: Manager for role-related operations.
        """
        self.doctrine_manager = doctrine_manager
        self.role_manager = role_manager

    def build_hierarchy(
        self, 
        doctrine_name: str, 
        character_id_to_name: Dict[str, str]
    ) -> List[Dict[str, Any]]:
        """
        Builds a structured hierarchy of Doctrine -> Roles -> Characters.

        :param doctrine_name: The name of the selected doctrine to build the tree for.
        :param character_id_to_name: A mapping from character ID to character name.
        :return: A list containing the structured hierarchy dictionary.
        """
        hierarchy = []

        # 1. Find the selected doctrine
        target_doctrine = None
        for doctrine in self.doctrine_manager.list_doctrines():
            if doctrine.get('doctrine_name') == doctrine_name:
                target_doctrine = doctrine
                break

        if not target_doctrine:
            return hierarchy

        d_uid = target_doctrine['doctrine_uid']
        d_name = target_doctrine['doctrine_name']

        # 2. Initialize Doctrine node
        doctrine_node = {
            "type": "Doctrine",
            "uid": d_uid,
            "name": d_name,
            "roles": []
        }

        # 3. Retrieve character assignments for this doctrine
        # Note: doctrine_manager returns mapping of str(role_uid) -> List[char_id]
        role_assignments = self.doctrine_manager.get_character_assignments(d_uid)
        doctrine_roles = target_doctrine.get('roles', [])

        for r_uid in doctrine_roles:
            # Get role name from role_manager
            role_info = self.role_manager.roles.get(r_uid, {})
            role_name = role_info.get('role_name') or f"Role (UID: {r_uid})"

            # 4. Initialize Role node
            role_node = {
                "type": "Role",
                "uid": r_uid,
                "name": role_name,
                "characters": []
            }

            # 5. Get characters assigned to this role
            # The manager uses string representation of UID for lookup
            chars = role_assignments.get(str(r_uid), [])
            for char_id in chars:
                char_name = character_id_to_name.get(char_id) or char_id
                
                # 6. Initialize Character node
                role_node["characters"].append({
                    "type": "Character",
                    "uid": char_id,
                    "name": char_name
                })

            doctrine_node["roles"].append(role_node)

        hierarchy.append(doctrine_node)
        return hierarchy
