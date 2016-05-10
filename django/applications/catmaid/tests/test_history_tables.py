from django.db import connection
from django.test import TestCase
from guardian.shortcuts import assign_perm
from catmaid.models import Project, User


class HistoryTableTests(TestCase):
    """Test the history table implementation, expecting an empty database.
    """

    def setUp(self):
        """Create a project and a suer with browse/annotate permissions on
        it. Both are referenced when creating
        """
        self.user = User.objects.create(username="test", password="test")
        self.admin = User.objects.create(username="admin", password="admin", is_superuser=True)
        self.project = Project.objects.create(title="Testproject")
        assign_perm('can_browse', self.user, self.project)
        assign_perm('can_annotate', self.user, self.project)

    def test_history_table_existence(self):
        """Test if all catmaid tables have a history table"""
        expected_tables_with_history = (
            'broken_slice',
            'cardinality_restriction',
            'catmaid_userprofile',
            'catmaid_volume',
            'change_request',
            'class',
            'class_class',
            'class_instance',
            'class_instance_class_instance',
            'client_data',
            'client_datastore',
            'concept',
            'connector',
            'connector_class_instance',
            'data_view',
            'data_view_type',
            'location',
            'log',
            'message',
            'overlay',
            'project',
            'project_stack',
            'region_of_interest',
            'region_of_interest_class_instance',
            'relation',
            'relation_instance',
            'restriction',
            'review',
            'reviewer_whitelist',
            'stack',
            'stack_class_instance',
            'suppressed_virtual_treenode',
            'textlabel',
            'textlabel_location',
            'treenode',
            'treenode_class_instance',
            'treenode_connector'
        )

        cursor = connection.cursor()
        cmt_template = ",".join(('(%s)',) * len(expected_tables_with_history))
        cursor.execute("""
            SELECT cmt.table_name, cht.history_table_name, COUNT(cmt.table_name), COUNT(cht.history_table_name),
            FROM catmaid_history_table cht
            JOIN (VALUES {}) cmt(table_name) ON cmt.table_name = cht.live_table_name
            GROUP BY cmt.table_name, cht.history_table_name
        """.format(cmt_template), expected_tables_with_history)

        # Expect exactly one history table for all the specified CATMAID tables
        table_info = cursor.fetchall()
        self.assertEqual(len(table_info), len(expected_tables_with_history))



    def test_insert(self):
        return
        cursor = connection.cursor()
        cursor.execute("""

                       """)
