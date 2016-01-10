import re
import urllib
import json

from django.conf import settings
from django.contrib.auth.models import Permission
from django.db import connection, transaction
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.test import TestCase, TransactionTestCase
from django.test.client import Client
from guardian.shortcuts import assign_perm

from catmaid.fields import Double3D, Integer3D
from catmaid.models import Project, Stack, ProjectStack
from catmaid.models import ClassInstance, Log, Message, TextlabelLocation
from catmaid.models import Treenode, Connector, TreenodeConnector, User, Review, ReviewerWhitelist
from catmaid.models import Textlabel, TreenodeClassInstance, ClassInstanceClassInstance
from catmaid.control.common import get_relation_to_id_map, get_class_to_id_map
from catmaid.control.neuron_annotations import _annotate_entities, create_annotation_query


class TransactionTests(TransactionTestCase):
    fixtures = ['catmaid_testdata']

    maxDiff = None

    def setUp(self):
        self.test_project_id = 3
        self.client = Client()

    def fake_authentication(self, username='test2', password='test'):
        self.client.login(username=username, password=password)

        user = User.objects.get(username=username)
        # Assign the new user permissions to browse and annotate projects
        p = Project.objects.get(pk=self.test_project_id)
        assign_perm('can_browse', user, p)
        assign_perm('can_annotate', user, p)

    def test_fail_unexpectedly(self):
        @transaction.atomic
        def insert_user():
            User(username='matri', password='boop').save()
            raise Exception()
            return HttpResponse(json.dumps({'should not': 'return this'}))

        User.objects.all().delete()
        with self.assertRaises(Exception):
            insert_user()
        self.assertEqual(0, User.objects.all().count())

    def test_remove_neuron_and_skeleton(self):
        """ The skeleton removal involves manual transaction management.
        Therefore, we need to make its test part of a TransactionTest.
        """
        self.fake_authentication()
        skeleton_id = 1
        neuron_id = 2

        count_logs = lambda: Log.objects.all().count()
        log_count = count_logs()
        response = self.client.post(
                '/%d/neuron/%s/delete' % (self.test_project_id, neuron_id), {})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
            'skeleton_ids': [skeleton_id],
            'success': 'Deleted neuron #2 as well as its skeletons and annotations.'
        }
        self.assertEqual(expected_result, parsed_response)

        self.assertEqual(0, Treenode.objects.filter(skeleton_id=skeleton_id).count())
        self.assertEqual(0, ClassInstanceClassInstance.objects.filter(class_instance_b=neuron_id).count())
        self.assertEqual(0, ClassInstance.objects.filter(id=skeleton_id).count())
        self.assertEqual(0, ClassInstance.objects.filter(id=neuron_id).count())
        self.assertEqual(0, TreenodeClassInstance.objects.filter(class_instance=skeleton_id).count())
        # This is a TCI related to a treenode included in the skeleton
        self.assertEqual(0, TreenodeClassInstance.objects.filter(id=353).count())

        self.assertEqual(log_count + 1, count_logs())


class InsertionTest(TestCase):
    """ This test case insers various model objects and tests if this is done as
    expected. No fixture data is needed for this test.
    """
    fixtures = ['catmaid_smallenv']
    maxDiff = None

    def insert_project(self):
        p = Project()
        p.title = "Example Project"
        p.comment = "This is an example project for the Django tests"
        p.save()
        return p

    def insert_stack(self):
        s = Stack()
        s.title = "Example Stack"
        s.image_base = "http://incf.ini.uzh.ch/image-stack-fib/"
        s.trakem2_project = False
        s.dimension = Integer3D(x=2048, y=1536, z=460)
        s.resolution = Double3D(x=5.0001, y=5.0002, z=9.0003)
        s.num_zoom_levels = -1
        s.file_extension = 'jpg'
        s.tile_width = 256
        s.tile_height = 256
        s.tile_source_type = 1
        s.save()
        return s

    def test_project_insertion(self):
        """
        Tests that a project can be inserted, and that the
        id is retrievable afterwards.  (This is something that
        the custom psycopg2 driver is needed for.)
        """
        p = self.insert_project()
        self.assertIsInstance(p.id, (int, long))

    def test_stack_insertion(self):
        p = self.insert_project()
        s = self.insert_stack()
        self.assertTrue(Project.objects.get(pk=p.id))
        self.assertTrue(Stack.objects.get(pk=s.id))
        # Now try to associate this stack with the project:
        ps = ProjectStack(project=p, stack=s)
        ps.save()

        self.assertEqual(p.stacks.count(), 1)


class RelationQueryTests(TestCase):
    fixtures = ['catmaid_testdata']

    maxDiff = None

    def setUp(self):
        self.test_project_id = 3

    def test_find_all_neurons(self):
        all_neurons = ClassInstance.objects.filter(class_column__class_name='neuron',
                                                   project=self.test_project_id)
        self.assertEqual(all_neurons.count(), 11)

    def test_find_downstream_neurons(self):
        upstream = ClassInstance.objects.get(name='branched neuron')
        self.assertTrue(upstream)

        skeletons = ClassInstance.objects.filter(
            class_column__class_name='skeleton',
            cici_via_a__relation__relation_name='model_of',
            cici_via_a__class_instance_b=upstream)

        downstreams = list(upstream.all_neurons_downstream(self.test_project_id, skeletons))
        self.assertEqual(len(downstreams), 2)

        self.assertEqual(downstreams[0]['name'], "downstream-A / skeleton 373")
        self.assertEqual(downstreams[0]['id__count'], 2)
        self.assertEqual(downstreams[1]['name'], "downstream-B / skeleton 361")
        self.assertEqual(downstreams[1]['id__count'], 1)

    def test_find_upstream_neurons(self):
        downstream = ClassInstance.objects.get(name='downstream-A')
        self.assertTrue(downstream)

        skeletons = ClassInstance.objects.filter(
            class_column__class_name='skeleton',
            cici_via_a__relation__relation_name='model_of',
            cici_via_a__class_instance_b=downstream)

        upstreams = list(downstream.all_neurons_upstream(self.test_project_id, skeletons))
        self.assertEqual(upstreams[0]['name'], "branched neuron / skeleton 235")

swc_output_for_skeleton_235 = '''237 0 1065 3035 0 0 -1
417 0 4990 4200 0 0 415
415 0 5810 3950 0 0 289
289 0 6210 3480 0 0 285
285 0 6100 2980 0 0 283
283 0 5985 2745 0 0 281
281 0 5675 2635 0 0 279
277 0 6090 1550 0 0 275
275 0 5800 1560 0 0 273
273 0 5265 1610 0 0 271
271 0 5090 1675 0 0 269
279 0 5530 2465 0 0 267
267 0 5400 2200 0 0 265
269 0 4820 1900 0 0 265
265 0 4570 2125 0 0 263
261 0 2820 1345 0 0 259
259 0 3445 1385 0 0 257
257 0 3825 1480 0 0 255
255 0 3850 1790 0 0 253
263 0 3915 2105 0 0 253
253 0 3685 2160 0 0 251
251 0 3380 2330 0 0 249
249 0 2815 2590 0 0 247
247 0 2610 2700 0 0 245
245 0 1970 2595 0 0 243
243 0 1780 2570 0 0 241
241 0 1340 2660 0 0 239
239 0 1135 2800 0 0 237
'''


def swc_string_to_sorted_matrix(s):
    m = [re.split("\s+", x) for x in s.splitlines() if not re.search('^\s*(#|$)', x)]
    return sorted(m, key=lambda x: x[0])


class ViewPageTests(TestCase):
    fixtures = ['catmaid_testdata']

    maxDiff = None

    def setUp(self):
        """ Creates a new test client and test user. The user is assigned
        permissions to modify an existing test project.
        """
        self.test_project_id = 3
        self.test_user_id = 3
        self.client = Client()

        p = Project.objects.get(pk=self.test_project_id)

        user = User.objects.get(pk=3)
        # Assign the new user permissions to browse and annotate projects
        assign_perm('can_browse', user, p)
        assign_perm('can_annotate', user, p)

    def fake_authentication(self, username='test2', password='test', add_default_permissions=False):
        self.client.login(username=username, password=password)

        if add_default_permissions:
            p = Project.objects.get(pk=self.test_project_id)
            user = User.objects.get(username=username)
            # Assign the new user permissions to browse and annotate projects
            assign_perm('can_browse', user, p)
            assign_perm('can_annotate', user, p)

    def compare_swc_data(self, s1, s2):
        m1 = swc_string_to_sorted_matrix(s1)
        m2 = swc_string_to_sorted_matrix(s2)
        self.assertEqual(len(m1), len(m2))

        fields = ['id', 'type', 'x', 'y', 'z', 'radius', 'parent']
        d = dict((x, i) for (i, x) in enumerate(fields))

        for i, e1 in enumerate(m1):
            e2 = m2[i]
            for f in ('id', 'parent', 'type'):
                self.assertEqual(e1[d[f]], e2[d[f]])
            for f in ('x', 'y', 'z', 'radius'):
                self.assertAlmostEqual(float(e1[d[f]]),
                                  float(e2[d[f]]))

    def test_authentication(self):
        # Try to access the password change view without logging in
        response = self.client.get('/user/password_change/')
        self.assertRedirects(response, '/accounts/login?next=/user/password_change/')
        self.assertEqual(response.status_code, 302)
        # Now insert a fake session and expect a successful request
        self.fake_authentication()
        response = self.client.get('/user/password_change/')
        self.assertEqual(response.status_code, 200)

    def test_token_authentication(self):
        response = self.client.post('/api-token-auth/',
                {'username': 'test2',
                 'password': 'test'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        token = parsed_response['token']

        token_client = Client(enforce_csrf_checks=True)
        sess_client = Client(enforce_csrf_checks=True)
        sess_client.login(username='test2', password='test')

        # Check that a Django view rejects an unauthed request...
        response = token_client.post('/%d/node/user-info' % (self.test_project_id,),
                {'node_id': 383})
        self.assertEqual(response.status_code, 403)
        # ..and an authed session request without CSRF headers...
        response = sess_client.post('/%d/node/user-info' % (self.test_project_id,),
                {'node_id': 383})
        self.assertEqual(response.status_code, 403)
        # ...but accepts a token auth request without CSRF
        response = token_client.post('/%d/node/user-info' % (self.test_project_id,),
                {'node_id': 383},
                HTTP_X_AUTHORIZATION='Token ' + token)
        self.assertEqual(response.status_code, 200)

        # Check that a DRF view rejects an unauthed request...
        response = token_client.post('/%d/annotations/' % (self.test_project_id,))
        self.assertEqual(response.status_code, 403)
        # ..and an authed session request without CSRF headers...
        response = sess_client.post('/%d/annotations/' % (self.test_project_id,))
        self.assertEqual(response.status_code, 403)
        # ...but accepts a token auth request without CSRF
        response = token_client.post('/%d/annotations/' % (self.test_project_id,),
                HTTP_X_AUTHORIZATION='Token ' + token)
        self.assertEqual(response.status_code, 200)

    def test_user_project_permissions_not_logged_in(self):
        response = self.client.get('/permissions')
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [{}, []]
        self.assertEqual(expected_result, parsed_response)

    def test_user_project_permissions(self):
        self.fake_authentication()
        response = self.client.get('/permissions')
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
            {'can_administer': {'3': False},
             'add_project': {'3': False},
             'can_annotate': {'3': True},
             'change_project': {'3': False},
             'can_browse': {'3': True},
             'delete_project': {'3': False}}, [u'test1']]
        self.assertEqual(expected_result, parsed_response)

    def test_swc_file(self):
        self.fake_authentication()
        url = '/%d/skeleton/235/swc' % (self.test_project_id,)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.compare_swc_data(response.content, swc_output_for_skeleton_235)

    def test_labels(self):
        self.fake_authentication()
        response = self.client.get('/%d/labels/' % (self.test_project_id,))
        self.assertEqual(response.status_code, 200)
        returned_labels = json.loads(response.content)
        self.assertEqual(set(returned_labels),
                         set(["t",
                              "synapse with more targets",
                              "uncertain end",
                              "TODO"]))
        nods = {"7": "7",
                "237": "237",
                "367": "367",
                "377": "377",
                "417": "417",
                "409": "409",
                "407": "407",
                "399": "399",
                "397": "397",
                "395": "395",
                "393": "393",
                "387": "387",
                "385": "385",
                "403": "403",
                "405": "405",
                "383": "383",
                "391": "391",
                "415": "415",
                "289": "289",
                "285": "285",
                "283": "283",
                "281": "281",
                "277": "277",
                "275": "275",
                "273": "273",
                "271": "271",
                "279": "279",
                "267": "267",
                "269": "269",
                "265": "265",
                "261": "261",
                "259": "259",
                "257": "257",
                "255": "255",
                "263": "263",
                "253": "253",
                "251": "251",
                "249": "249",
                "247": "247",
                "245": "245",
                "243": "243",
                "241": "241",
                "239": "239",
                "356": "356",
                "421": "421",
                "432": "432"}
        connector_ids = ("432",)
        response = self.client.post('/%d/labels-for-nodes' % (self.test_project_id,),
                              {'treenode_ids': ",".join(nods.keys()),
                               'connector_ids': ",".join(connector_ids)})

        returned_node_map = json.loads(response.content)
        self.assertEqual(len(returned_node_map.keys()), 3)
        self.assertEqual(set(returned_node_map['403']),
                         set(["uncertain end"]))
        self.assertEqual(set(returned_node_map['261']),
                         set(["TODO"]))
        self.assertEqual(set(returned_node_map['432']),
                         set(["synapse with more targets", "TODO"]))

        response = self.client.post('/%d/labels-for-node/location/%d' % (self.test_project_id,
                                                                    432))
        returned_labels = json.loads(response.content)
        self.assertEqual(set(returned_labels),
                         set(["synapse with more targets", "TODO"]))

        response = self.client.post('/%d/labels-for-node/treenode/%d' % (self.test_project_id,
                                                                    403))
        returned_labels = json.loads(response.content)
        self.assertEqual(len(returned_labels), 1)
        self.assertEqual(returned_labels[0], "uncertain end")

        # Test label update with, removing existing tags
        response = self.client.post('/%d/label/treenode/%d/update' % (self.test_project_id,
                                                                      403),
                                    {'tags': ",".join(['foo', 'bar'])})
        parsed_response = json.loads(response.content)
        self.assertTrue('message' in parsed_response)
        self.assertTrue(parsed_response['message'] == 'success')

        response = self.client.post('/%d/labels-for-node/treenode/%d' % (self.test_project_id,
                                                                    403))
        returned_labels = json.loads(response.content)
        self.assertEqual(len(returned_labels), 2)
        self.assertEqual(set(returned_labels), set(['foo', 'bar']))

        # Test label update without removing existing tags
        response = self.client.post('/%d/label/treenode/%d/update' % (self.test_project_id,
                                                                      403),
                                    {'tags': ",".join(['green', 'apple']),
                                     'delete_existing': 'false'})
        parsed_response = json.loads(response.content)
        self.assertTrue('message' in parsed_response)
        self.assertTrue(parsed_response['message'] == 'success')

        response = self.client.post('/%d/labels-for-node/treenode/%d' % (self.test_project_id,
                                                                    403))
        returned_labels = json.loads(response.content)
        self.assertEqual(len(returned_labels), 4)
        self.assertEqual(set(returned_labels), set(['foo', 'bar', 'green', 'apple']))

        # Test removal of a single label
        response = self.client.post('/%d/label/treenode/%d/remove' % (self.test_project_id,
                                                                      403),
                                    {'tag': 'bar'})
        parsed_response = json.loads(response.content)
        self.assertTrue('message' in parsed_response)
        self.assertTrue(parsed_response['message'] == 'success')

        response = self.client.post('/%d/labels-for-node/treenode/%d' % (self.test_project_id,
                                                                    403))
        returned_labels = json.loads(response.content)
        self.assertEqual(len(returned_labels), 3)
        self.assertEqual(set(returned_labels), set(['foo', 'green', 'apple']))

    def test_project_list(self):
        # Check that, pre-authentication, we can see none of the
        # projects:
        response = self.client.get('/projects/')
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(len(result), 0)

        # Add permission to the anonymous user to browse two projects
        anon_user = User.objects.get(pk=settings.ANONYMOUS_USER_ID)
        p = Project.objects.get(pk=self.test_project_id)
        assign_perm('can_browse', anon_user, p)

        # Check that, pre-authentication, we can see two of the
        # projects:
        response = self.client.get('/projects/')
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(len(result), 1)

        # Check stacks:
        stacks = result[0]['stacks']
        self.assertEqual(len(stacks), 1)

        # Check stacks groups
        stackgroups = result[0]['stackgroups']
        self.assertEqual(len(stackgroups), 0)

        # Now log in and check that we see a different set of projects:
        self.fake_authentication()

        # Add permission to the test  user to browse three projects
        test_user = User.objects.get(pk=self.test_user_id)
        for pid in (1,2,3,5):
            p = Project.objects.get(pk=pid)
            assign_perm('can_browse', test_user, p)

        # We expect four projects, one of them (project 2) is empty.
        response = self.client.get('/projects/')
        self.assertEqual(response.status_code, 200)
        result = json.loads(response.content)
        self.assertEqual(len(result), 4)

        def get_project(result, pid):
            rl = [r for r in result if r['id'] == pid]
            if len(rl) != 1:
                raise ValueError("Malformed result")
            return rl[0]

        # Check the first project:
        stacks = get_project(result, 1)['stacks']
        self.assertEqual(len(stacks), 1)

        # Check the second project:
        stacks = get_project(result, 3)['stacks']
        self.assertEqual(len(stacks), 1)

        # Check the third project:
        stacks = get_project(result, 5)['stacks']
        self.assertEqual(len(stacks), 2)

    def test_client_datastores(self):
        url = '/client/datastores/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue(parsed_response['permission_error'])

        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue(parsed_response['permission_error'])

        # Test basic datastore creation.
        self.fake_authentication()
        name = 'test-  %% datastore'
        response = self.client.post(url, {'name': name})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue('error' in parsed_response)
        name = 'test-datastore'
        response = self.client.post(url, {'name': name})
        self.assertEqual(response.status_code, 200)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        parsed_response = [p for p in parsed_response if p['name'] == name]
        self.assertEqual(len(parsed_response), 1)
        response = self.client.delete(url + name)
        self.assertEqual(response.status_code, 403)

        # Create data entries.
        url = url + name + '/'
        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'value': '{"json": false'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 400)
        parsed_response = json.loads(response.content)
        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'value': '{"json": true, "scope": "user-instance"}'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertFalse('error' in parsed_response)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(len(parsed_response), 1)
        self.assertEqual(parsed_response[0]['key'], 'test a')

        # Test that PUTting the same key replaces the value.
        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'value': '{"json": true, "scope": "user-instance", "replaced": true}'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 204)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(len(parsed_response), 1)
        value = json.loads(parsed_response[0]['value'])
        self.assertTrue(value['replaced'])

        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'project_id': self.test_project_id,
                    'value': '{"json": true, "scope": "user-project"}'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertFalse('error' in parsed_response)
        # Omitting project ID should return only global and user-instance keys.
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(len(parsed_response), 1)
        response = self.client.get(url, {'project_id': self.test_project_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(len(parsed_response), 2)
        self.assertEqual(parsed_response[0]['key'], 'test a')
        self.assertEqual(parsed_response[1]['key'], 'test a')

        # Test that a non-admin user cannot change global data.
        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'ignore_user': 'true',
                    'value': '{"json": true, "scope": "global-instance"}'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 403)

        response = self.client.put(
                url,
                urllib.urlencode({
                    'key': 'test a',
                    'ignore_user': 'true',
                    'project_id': self.test_project_id,
                    'value': '{"json": true, "scope": "global-instance"}'}),
                content_type='application/x-www-form-urlencoded')
        self.assertEqual(response.status_code, 403)

    def test_rename_neuron(self):
        self.fake_authentication()
        neuron_id = 233

        count_logs = lambda: Log.objects.all().count()
        log_count = count_logs()
        old_name = ClassInstance.objects.get(id=neuron_id).name
        new_name = 'newname'
        self.assertFalse(old_name == new_name)

        url = '/%d/neurons/%s/rename' % (self.test_project_id, neuron_id)
        response = self.client.post(url, {'name': new_name})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
            'success': True,
            'renamed_neuron': neuron_id
        }
        self.assertEqual(expected_result, parsed_response)

        self.assertEqual(new_name, ClassInstance.objects.get(id=neuron_id).name)
        self.assertEqual(log_count + 1, count_logs())

    def test_rename_neuron_fail(self):
        self.fake_authentication()
        neuron_id = 362

        # Lock this neuron for another user
        _annotate_entities(self.test_project_id, [neuron_id], {'locked': 1})

        count_logs = lambda: Log.objects.all().count()
        log_count = count_logs()
        old_name = ClassInstance.objects.get(id=neuron_id).name
        new_name = 'newname'
        self.assertFalse(old_name == new_name)

        url = '/%d/neurons/%s/rename' % (self.test_project_id, neuron_id)
        response = self.client.post(url, {'name': new_name})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue('error' in parsed_response)
        self.assertTrue(parsed_response['error'])

        self.assertEqual(old_name, ClassInstance.objects.get(id=neuron_id).name)
        self.assertEqual(log_count, count_logs())

    def test_skeletons_from_neuron(self):
        self.fake_authentication()
        url = '/%d/neuron/%d/get-all-skeletons' % (self.test_project_id,
                                              233)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        parsed_data = json.loads(response.content)
        self.assertEqual(len(parsed_data), 1)
        self.assertEqual(parsed_data[0], 235)

    def test_index(self):
        self.fake_authentication()
        url = '/'
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

    def test_user_list(self):
        self.fake_authentication()
        response = self.client.get('/user-list')
        expected_result = [
            {
                u'first_name': u'Admin',
                u'last_name': u'Superuser',
                u'color': [0.0, 1.0, 1.0],
                u'full_name': u'Admin Superuser',
                u'login': u'admin',
                u'id': 4
            },
            {
                u'first_name': u'Anonymous',
                u'last_name': u'User',
                u'color': [1.0, 0.0, 0.0],
                u'full_name': u'Anonymous User',
                u'login': u'AnonymousUser',
                u'id': -1
            }, {
                u'first_name': u'Test',
                u'last_name': u'User 0',
                u'color': [0.0, 1.0, 0.0],
                u'full_name': u'Test User 0',
                u'login': u'test0',
                u'id': 1
            }, {
                u'first_name': u'Test',
                u'last_name': u'User 1',
                u'color': [0.0, 0.0, 1.0],
                u'full_name': u'Test User 1',
                u'login': u'test1',
                u'id': 2
            }, {
                u'first_name': u'Test',
                u'last_name': u'User 2',
                u'color': [1.0, 0.0, 1.0],
                u'full_name': u'Test User 2',
                u'login': u'test2',
                u'id': 3
            }
        ]

        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response)

    def test_skeleton_root(self):
        self.fake_authentication()
        response = self.client.get('/%d/skeletons/%d/root' % (self.test_project_id, 235))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(parsed_response['root_id'], 237)
        self.assertAlmostEqual(parsed_response['x'], 1065)
        self.assertAlmostEqual(parsed_response['y'], 3035)
        self.assertAlmostEqual(parsed_response['z'], 0)

    def test_treenode_stats(self):
        self.fake_authentication()
        response = self.client.get('/%d/stats/nodecount' % (self.test_project_id,))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        values = parsed_response['values']
        users = parsed_response['users']
        values_and_users = zip(values, users)
        for t in values_and_users:
            if t[0] == 4:
                self.assertEqual(t[1], 'test0 (4)')
            elif t[0] == 2:
                self.assertEqual(t[1], 'test1 (2)')
            elif t[0] == 83:
                self.assertEqual(t[1], 'test2 (83)')
            else:
                raise Exception("Unexpected value in returned stats: " + str(t))

    def test_stats_summary(self):
        self.fake_authentication()
        response = self.client.get('/%d/stats/summary' % (self.test_project_id,))
        self.assertEqual(response.status_code, 200)
        expected_result = {
            u"connectors_created": 0,
            u'skeletons_created': 0,
            u'treenodes_created': 0,
        }
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response)

    def test_list_treenode_table_simple(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/treenode/table/%d/content' % (self.test_project_id, 235))
        self.assertEqual(response.status_code, 200)
        expected_result = [[
                [417, 415, 5, 4990.0, 4200.0, 0.0, -1.0, 3, 1323093096.0],
                [415, 289, 5, 5810.0, 3950.0, 0.0, -1.0, 3, 1323093096.0],
                [289, 285, 5, 6210.0, 3480.0, 0.0, -1.0, 3, 1323093096.0],
                [285, 283, 5, 6100.0, 2980.0, 0.0, -1.0, 3, 1323093096.0],
                [283, 281, 5, 5985.0, 2745.0, 0.0, -1.0, 3, 1323093096.0],
                [281, 279, 5, 5675.0, 2635.0, 0.0, -1.0, 3, 1323093096.0],
                [279, 267, 5, 5530.0, 2465.0, 0.0, -1.0, 3, 1323093096.0],
                [277, 275, 5, 6090.0, 1550.0, 0.0, -1.0, 3, 1323093096.0],
                [275, 273, 5, 5800.0, 1560.0, 0.0, -1.0, 3, 1323093096.0],
                [273, 271, 5, 5265.0, 1610.0, 0.0, -1.0, 3, 1323093096.0],
                [271, 269, 5, 5090.0, 1675.0, 0.0, -1.0, 3, 1323093096.0],
                [269, 265, 5, 4820.0, 1900.0, 0.0, -1.0, 3, 1323093096.0],
                [267, 265, 5, 5400.0, 2200.0, 0.0, -1.0, 3, 1323093096.0],
                [265, 263, 5, 4570.0, 2125.0, 0.0, -1.0, 3, 1323093096.0],
                [263, 253, 5, 3915.0, 2105.0, 0.0, -1.0, 3, 1323093096.0],
                [261, 259, 5, 2820.0, 1345.0, 0.0, -1.0, 3, 1323093096.0],
                [259, 257, 5, 3445.0, 1385.0, 0.0, -1.0, 3, 1323093096.0],
                [257, 255, 5, 3825.0, 1480.0, 0.0, -1.0, 3, 1323093096.0],
                [255, 253, 5, 3850.0, 1790.0, 0.0, -1.0, 3, 1323093096.0],
                [253, 251, 5, 3685.0, 2160.0, 0.0, -1.0, 3, 1323093096.0],
                [251, 249, 5, 3380.0, 2330.0, 0.0, -1.0, 3, 1323093096.0],
                [249, 247, 5, 2815.0, 2590.0, 0.0, -1.0, 3, 1323093096.0],
                [247, 245, 5, 2610.0, 2700.0, 0.0, -1.0, 3, 1323093096.0],
                [245, 243, 5, 1970.0, 2595.0, 0.0, -1.0, 3, 1323093096.0],
                [243, 241, 5, 1780.0, 2570.0, 0.0, -1.0, 3, 1323093096.0],
                [241, 239, 5, 1340.0, 2660.0, 0.0, -1.0, 3, 1323093096.0],
                [239, 237, 5, 1135.0, 2800.0, 0.0, -1.0, 3, 1323093096.0],
                [237, None, 5, 1065.0, 3035.0, 0.0, -1.0, 3, 1323093096.0]],
            [], [[261, 'TODO']]]
        parsed_response = json.loads(response.content)

        # Check each aaData row instead of everything at once for more granular
        # error reporting. Don't expext the same ordering.
        for (expected, parsed) in zip(sorted(expected_result[0]), sorted(parsed_response[0])):
            self.assertEqual(expected, parsed)
        self.assertEqual(expected_result[1], parsed_response[1])
        self.assertEqual(expected_result[2], parsed_response[2])

    def test_list_treenode_table_empty(self):
        self.fake_authentication()
        response = self.client.post('/%d/treenode/table/%d/content' % \
                                    (self.test_project_id, 0))
        self.assertEqual(response.status_code, 200)
        expected_result = [[], [], []]
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response)

    def test_fail_update_confidence(self):
        treenode_id = Treenode.objects.order_by("-id")[0].id + 1  # Inexistant
        self.fake_authentication()
        response = self.client.post(
                '/%d/treenodes/%d/confidence' % (self.test_project_id, treenode_id),
                {'new_confidence': '4'})
        self.assertEqual(response.status_code, 200)
        expected_result = 'No skeleton and neuron for treenode %s' % treenode_id
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response['error'])

    def test_update_confidence_of_treenode(self):
        treenode_id = 7
        self.fake_authentication()
        response = self.client.post(
                '/%d/treenodes/%d/confidence' % (self.test_project_id, treenode_id),
                {'new_confidence': '4'})
        self.assertEqual(response.status_code, 200)
        treenode = Treenode.objects.filter(id=treenode_id).get()
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(4, treenode.confidence)

        response = self.client.post(
                '/%d/treenodes/%d/confidence' % (self.test_project_id, treenode_id),
                {'new_confidence': '5'})
        self.assertEqual(response.status_code, 200)
        treenode = Treenode.objects.filter(id=treenode_id).get()
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(5, treenode.confidence)

    def test_update_confidence_of_treenode_connector(self):
        treenode_id = 285
        treenode_connector_id = 360
        self.fake_authentication()
        response = self.client.post(
                '/%d/treenodes/%d/confidence' % (self.test_project_id, treenode_id),
                {'new_confidence': '4', 'to_connector': 'true'})
        self.assertEqual(response.status_code, 200)
        connector = TreenodeConnector.objects.filter(id=treenode_connector_id).get()
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(4, connector.confidence)

        response = self.client.post(
                '/%d/treenodes/%d/confidence' % (self.test_project_id, treenode_id),
                {'new_confidence': '5', 'to_connector': 'true'})
        self.assertEqual(response.status_code, 200)
        connector = TreenodeConnector.objects.filter(id=treenode_connector_id).get()
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(5, connector.confidence)

    def test_list_connector_empty(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/table/list' % self.test_project_id, {
                    'iDisplayStart': 0,
                    'iDisplayLength': 25,
                    'iSortingCols': 1,
                    'iSortCol_0': 0,
                    'sSortDir_0': 'asc',
                    'relation_type': 1,
                    'skeleton_id': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'iTotalRecords': 0, 'iTotalDisplayRecords': 0, 'aaData': []}
        self.assertEqual(expected_result, parsed_response)

    def test_list_connector_outgoing_with_sorting_and_paging(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/table/list' % self.test_project_id, {
                    'iDisplayStart': 1,
                    'iDisplayLength': 2,
                    'iSortingCols': 1,
                    'iSortCol_0': 6,
                    'sSortDir_0': 'desc',
                    'relation_type': 1,
                    'skeleton_id': 235})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                u'iTotalRecords': 4,
                u'iTotalDisplayRecords': 4,
                u'aaData': [
                    [421, 373, 6630.00, 4330.00, 0.0, 0, 5, u"", 5, u"test2", 409, u'07-10-2011 07:02'],
                    [356, 373, 7620.00, 2890.00, 0.0, 0, 5, u"", 5, u"test2", 377, u'27-10-2011 10:45']]}
        self.assertEqual(expected_result, parsed_response)

    def test_list_connector_outgoing_with_sorting(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/table/list' % self.test_project_id, {
                    'iDisplayStart': 0,
                    'iDisplayLength': 25,
                    'iSortingCols': 1,
                    'iSortCol_0': 6,
                    'sSortDir_0': 'desc',
                    'relation_type': 1,
                    'skeleton_id': 235})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                u'iTotalRecords': 4,
                u'iTotalDisplayRecords': 4,
                u'aaData': [
                    [432, u"", 2640.00, 3450.00, 0.0, 0, 5, u"synapse with more targets, TODO", 0, u"test2", u"", u'31-10-2011 05:22'],
                    [421, 373, 6630.00, 4330.00, 0.0, 0, 5, u"", 5, u"test2", 409, u'07-10-2011 07:02'],
                    [356, 373, 7620.00, 2890.00, 0.0, 0, 5, u"", 5, u"test2", 377, u'27-10-2011 10:45'],
                    [356, 361, 7030.00, 1980.00, 0.0, 0, 5, u"", 9, u"test2", 367, u'27-10-2011 10:45']]
        }
        self.assertEqual(expected_result, parsed_response)

    def test_list_connector_incoming_with_connecting_skeletons(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/table/list' % self.test_project_id, {
                    'iDisplayStart': 0,
                    'iDisplayLength': 25,
                    'iSortingCols': 1,
                    'iSortCol_0': 0,
                    'sSortDir_0': 'asc',
                    'relation_type': 0,
                    'skeleton_id': 373})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                u'iTotalRecords': 2,
                u'iTotalDisplayRecords': 2,
                u'aaData': [
                    [356, 235, 6100.00, 2980.00, 0.0, 0, 5, u"", 28,
                     u"test2", 285, u'27-10-2011 10:45'],
                    [421, 235, 5810.00, 3950.00, 0.0, 0, 5, u"", 28,
                     u"test2", 415, u'07-10-2011 07:02']]}
        self.assertEqual(expected_result, parsed_response)

    def test_one_to_many_skeletons_connector_list(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/list/one_to_many' % self.test_project_id, {
                    'skid': 373,
                    'skids[0]': 235,
                    'relation': 'presynaptic_to'
                })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []

        self.assertEqual(expected_result, parsed_response)

        response = self.client.post(
                '/%d/connector/list/one_to_many' % self.test_project_id, {
                    'skid': 373,
                    'skids[0]': 235,
                    'relation': 'postsynaptic_to'
                })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[356, [6730.0, 2700.0, 0.0],
                            377, 373, 5, 3, [7620.0, 2890.0, 0.0],
                            285, 235, 5, 3, [6100.0, 2980.0, 0.0]],
                           [421, [6260.0, 3990.0, 0.0],
                            409, 373, 5, 3, [6630.0, 4330.0, 0.0],
                            415, 235, 5, 3, [5810.0, 3950.0, 0.0]]]
        self.assertEqual(expected_result, parsed_response)

    def test_many_to_many_skeletons_connector_list(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/connector/list/many_to_many' % self.test_project_id, {
                    'skids1[0]': 373,
                    'skids2[0]': 235,
                    'relation': 'presynaptic_to'
                })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []

        self.assertEqual(expected_result, parsed_response)

        response = self.client.post(
                '/%d/connector/list/many_to_many' % self.test_project_id, {
                    'skids1[0]': 373,
                    'skids2[0]': 235,
                    'relation': 'postsynaptic_to'
                })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[356, [6730.0, 2700.0, 0.0],
                            377, 373, 5, 3, [7620.0, 2890.0, 0.0],
                            285, 235, 5, 3, [6100.0, 2980.0, 0.0]],
                           [421, [6260.0, 3990.0, 0.0],
                            409, 373, 5, 3, [6630.0, 4330.0, 0.0],
                            415, 235, 5, 3, [5810.0, 3950.0, 0.0]]]
        self.assertEqual(expected_result, parsed_response)

    def test_create_connector(self):
        self.fake_authentication()
        connector_count = Connector.objects.all().count()
        response = self.client.post(
                '/%d/connector/create' % self.test_project_id,
                {'x': 111, 'y': 222, 'z': 333, 'confidence': 3})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue('connector_id' in parsed_response.keys())
        connector_id = parsed_response['connector_id']

        new_connector = Connector.objects.filter(id=connector_id).get()
        self.assertEqual(111, new_connector.location_x)
        self.assertEqual(222, new_connector.location_y)
        self.assertEqual(333, new_connector.location_z)
        self.assertEqual(3, new_connector.confidence)
        self.assertEqual(connector_count + 1, Connector.objects.all().count())

    def test_delete_connector(self):
        self.fake_authentication()
        connector_id = 356
        connector = Connector.objects.get(id=connector_id)
        connector_count = Connector.objects.all().count()
        treenode_connector_count = TreenodeConnector.objects.all().count()
        response = self.client.post(
                '/%d/connector/delete' % self.test_project_id,
                {'connector_id': connector_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                'message': 'Removed connector and class_instances',
                'connector_id': 356}
        self.assertEqual(expected_result, parsed_response)

        self.assertEqual(connector_count - 1, Connector.objects.all().count())
        self.assertEqual(treenode_connector_count - 3, TreenodeConnector.objects.all().count())
        self.assertEqual(0, Connector.objects.filter(id=connector_id).count())
        self.assertEqual(0, TreenodeConnector.objects.filter(connector=connector).count())

    def test_delete_link_failure(self):
        self.fake_authentication()
        connector_id = 202020
        treenode_id = 202020

        tc_count = TreenodeConnector.objects.all().count()
        response = self.client.post(
                '/%d/link/delete' % self.test_project_id,
                {'connector_id': connector_id, 'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'error': 'Failed to delete connector #%s from geometry domain.' % connector_id}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(tc_count, TreenodeConnector.objects.all().count())

    def test_most_recent_treenode(self):
        self.fake_authentication()

        most_recent_node_id = 2423
        treenode_id = 2415

        response = self.client.post(
                '/%d/node/most_recent' % self.test_project_id,
                {'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                'id': most_recent_node_id,
                'x': 4140,
                'y': 6460,
                'z': 0,
                }
        self.assertEqual(expected_result, parsed_response)

    def test_update_textlabel(self):
        self.fake_authentication()

        textlabel_id = 1

        params = {
                'tid': textlabel_id,
                'pid': self.test_project_id,
                'x': 3,
                'y': 1,
                'z': 4,
                'r': 0,
                'g': 0,
                'b': 0,
                'a': 0,
                'type': 'text',
                'text': 'Lets dance the Grim Fandango!',
                'font_name': 'We may have years, we may have hours',
                'font_style': 'But sooner or later we all push up flowers',
                'font_size': 5555,
                'scaling': 0}

        response = self.client.post(
                '/%d/textlabel/update' % self.test_project_id,
                params)
        expected_result = ' '
        self.assertEqual(response.status_code, 200)
        self.assertEqual(expected_result, response.content)

        label = Textlabel.objects.filter(id=textlabel_id)[0]
        label_location = TextlabelLocation.objects.filter(textlabel=textlabel_id)[0]
        self.assertEqual(params['pid'], label.project_id)
        self.assertEqual(params['x'], label_location.location.x)
        self.assertEqual(params['y'], label_location.location.y)
        self.assertEqual(params['z'], label_location.location.z)
        self.assertEqual(params['type'], label.type)
        self.assertEqual(params['text'], label.text)
        self.assertEqual(params['font_name'], label.font_name)
        self.assertEqual(params['font_style'], label.font_style)
        self.assertEqual(params['font_size'], label.font_size)
        self.assertEqual(False, label.scaling)

    def test_update_textlabel_using_optionals(self):
        """
        Omits some parameters and ensures corresponding
        properties of label were unchanged.
        """
        self.fake_authentication()

        textlabel_id = 1

        params = {
                'tid': textlabel_id,
                'text': 'Almost faltering, we held on to each other so that neither of us touched the ground.',
                'type': 'bubble'}

        label_before_update = Textlabel.objects.filter(id=textlabel_id)[0]
        label_location_before_update = TextlabelLocation.objects.filter(textlabel=textlabel_id)[0]

        response = self.client.post(
                '/%d/textlabel/update' % self.test_project_id,
                params)
        expected_result = ' '
        self.assertEqual(response.status_code, 200)
        self.assertEqual(expected_result, response.content)

        label = Textlabel.objects.filter(id=textlabel_id)[0]
        label_location = TextlabelLocation.objects.filter(textlabel=textlabel_id)[0]
        self.assertEqual(label_before_update.project_id, label.project_id)
        self.assertEqual(label_location_before_update.location.x, label_location.location.x)
        self.assertEqual(label_location_before_update.location.y, label_location.location.y)
        self.assertEqual(label_location_before_update.location.z, label_location.location.z)
        self.assertEqual(params['type'], label.type)
        self.assertEqual(params['text'], label.text)
        self.assertEqual(label_before_update.font_name, label.font_name)
        self.assertEqual(label_before_update.font_style, label.font_style)
        self.assertEqual(label_before_update.font_size, label.font_size)
        self.assertEqual(label_before_update.scaling, label.scaling)

    def test_update_textlabel_failure(self):
        self.fake_authentication()

        textlabel_id = 404

        params = {'tid': textlabel_id, 'pid': self.test_project_id}

        response = self.client.post(
                '/%d/textlabel/update' % self.test_project_id,
                params)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = 'Failed to find Textlabel with id %s.' % textlabel_id
        self.assertIn('error', parsed_response)
        self.assertIn(expected_result, parsed_response['error'])

    def test_delete_textlabel(self):
        self.fake_authentication()

        textlabel_id = 1

        self.assertEqual(1, Textlabel.objects.filter(id=textlabel_id).count())
        self.assertEqual(1, TextlabelLocation.objects.filter(textlabel=textlabel_id).count())
        response = self.client.post(
                '/%d/textlabel/delete' % self.test_project_id,
                {'tid': textlabel_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'Success.'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(0, Textlabel.objects.filter(id=textlabel_id).count())
        self.assertEqual(0, TextlabelLocation.objects.filter(textlabel=textlabel_id).count())

    def test_create_textlabel(self):
        self.fake_authentication()

        label_data = [
                # param-name, param values
                ('text', ['baba tiki dido', 'doop op', '']),
                ('type', ['text', 'bubble', 'non-valid-type']),
                ('font_name', [False, False, 'Times New Roman']),
                ('font_style', [False, 'bold', 'italic']),
                ('font_size', [55, 4, False]),
                ('x', [1, 2, 3]),
                ('y', [1, 100, 233]),
                ('z', [1, 0, 555]),
                ('r', [1, 2, 3]),
                ('g', [3, 4, 5]),
                ('b', [5, 7, 9]),
                ('a', [225, 225, 225])]

        label_count = Textlabel.objects.all().count()
        # Create and test labels
        for i in range(len(label_data[0][1])):
            params = {}
            # Fill request with POST-data
            for p, values in label_data:
                if values[i]:
                    params[p] = values[i]
            response = self.client.post(
                    '/%d/textlabel/create' % self.test_project_id,
                    params)

            parsed_response = json.loads(response.content)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(label_count + 1 + i, Textlabel.objects.all().count())
            self.assertTrue('tid' in parsed_response.keys())
            label = get_object_or_404(Textlabel, id=parsed_response['tid'])
            label_location = TextlabelLocation.objects.get(textlabel=label.id)

            # For each attribute, ensure new label is in accord with input
            # label_location_data = Double3D(x=0, y=0, z=0)
            for p, values in label_data:
                value = values[i]
                if (value == False):
                    continue  # Do not check for default values for now

                if (p == 'type' and value != 'bubble'):
                    self.assertEqual('text', getattr(label, p))
                elif (p == 'text' and value == ''):
                    self.assertEqual('Edit this text...', getattr(label, p))
                elif (p in ['x', 'y', 'z']):
                    self.assertEqual(value, getattr(label_location.location, p))
                elif (p in ['r', 'g', 'b', 'a']):
                    # Model does not include textlabel colour at the moment
                    pass
                else:
                    self.assertEqual(value, getattr(label, p))
            # self.assertEqual(label_location_data, label_location.location)

    log_rows = [
                    [
                        'test2',
                        'create_neuron',
                        '22-07-2012 16:50',
                        5290.0,
                        3930.0,
                        279.0,
                        'Create neuron 2434 and skeleton 2433'],
                    [
                        'test2',
                        'create_neuron',
                        '22-07-2012 19:12',
                        4470.0,
                        2110.0,
                        180.0,
                        'Create neuron 2441 and skeleton 2440'],
                    [
                        'test2',
                        'create_neuron',
                        '22-07-2012 19:15',
                        3680.0,
                        2530.0,
                        180.0,
                        'Create neuron 2452 and skeleton 2451']
            ]

    def test_list_logs_user_param(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/logs/list' % self.test_project_id, {'user_id': 1})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                'iTotalDisplayRecords': 0,
                'iTotalRecords': 0,
                'aaData': []
                }
        self.assertEqual(expected_result, parsed_response)

    def test_list_logs_sort(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/logs/list' % self.test_project_id, {
                    'iSortingCols': 2,
                    'iSortCol_0': 5,  # z
                    'iSortDir_0': 'ASC',
                    'iSortCol_1': 3,  # x
                    'iSortDir_1': 'DESC'
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                'iTotalDisplayRecords': 3,
                'iTotalRecords': 3,
                'aaData': [
                    self.log_rows[0], self.log_rows[1], self.log_rows[2]
                    ]
                }
        self.assertEqual(expected_result, parsed_response)

    def test_list_logs_subset(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/logs/list' % self.test_project_id, {
                    'iDisplayStart': 1,
                    'iDisplayLength': 2
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(2, parsed_response['iTotalDisplayRecords'])
        self.assertEqual(2, parsed_response['iTotalRecords'])

    def test_list_logs_no_params(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/logs/list' % self.test_project_id, {})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(3, parsed_response['iTotalDisplayRecords'])
        self.assertEqual(3, parsed_response['iTotalRecords'])
        self.assertTrue(self.log_rows[0] in parsed_response['aaData'])
        self.assertTrue(self.log_rows[1] in parsed_response['aaData'])
        self.assertTrue(self.log_rows[2] in parsed_response['aaData'])

    def test_create_treenode(self):
        self.fake_authentication()
        relation_map = get_relation_to_id_map(self.test_project_id)
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        response = self.client.post('/%d/treenode/create' % self.test_project_id, {
            'x': 5,
            'y': 10,
            'z': 15,
            'confidence': 5,
            'parent_id': -1,
            'radius': 2})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        self.assertTrue('treenode_id' in parsed_response)
        self.assertTrue('skeleton_id' in parsed_response)

        self.assertEqual(treenode_count + 1, count_treenodes())
        self.assertEqual(skeleton_count + 1, count_skeletons())
        self.assertEqual(neuron_count + 1, count_neurons())

        treenode_skeleton_relation = TreenodeClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['element_of'],
                treenode=parsed_response['treenode_id'],
                class_instance=parsed_response['skeleton_id'])
        neuron_skeleton_relation = ClassInstanceClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['model_of'],
                class_instance_a=parsed_response['skeleton_id'])
        neuron_log = Log.objects.filter(
                project=self.test_project_id,
                operation_type='create_neuron')

        # FIXME: discussed in
        # https://github.com/catmaid/CATMAID/issues/754
        #self.assertEqual(1, treenode_skeleton_relation.count())
        self.assertEqual(1, neuron_skeleton_relation.count())
        # FIXME: This test doesn't work like expected
        #self.assertEqual(1, neuron_log.count())
        #neuron_log_location = neuron_log[0].location
        #self.assertEqual(5, neuron_log_location.x)
        #self.assertEqual(10, neuron_log_location.y)
        #self.assertEqual(15, neuron_log_location.z)

    def test_create_treenode2(self):
        self.fake_authentication()
        relation_map = get_relation_to_id_map(self.test_project_id)
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()
        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        response = self.client.post('/%d/treenode/create' % self.test_project_id, {
            'x': 5,
            'y': 10,
            'z': 15,
            'confidence': 5,
            'parent_id': -1,
            'radius': 2})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        self.assertTrue('treenode_id' in parsed_response)
        self.assertTrue('skeleton_id' in parsed_response)

        self.assertEqual(treenode_count + 1, count_treenodes())
        self.assertEqual(skeleton_count + 1, count_skeletons())
        self.assertEqual(neuron_count + 1, count_neurons())

        treenode_skeleton_relation = TreenodeClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['element_of'],
                treenode=parsed_response['treenode_id'],
                class_instance=parsed_response['skeleton_id'])
        neuron_skeleton_relation = ClassInstanceClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['model_of'],
                class_instance_a=parsed_response['skeleton_id'])
        # FIXME: Log test doesn't work like this, because we don't have the
        # neuron ID available
        #neuron_log = Log.objects.filter(
        #        project=self.test_project_id,
        #        operation_type='create_neuron',
        #        freetext='Create neuron %s and skeleton %s' % (parsed_response['neuron_id'], parsed_response['skeleton_id']))

        root = ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['root'])[0]

        self.assertEqual(1, neuron_skeleton_relation.count())
        #FIXME: These tests don't work like expected anymore
        #self.assertEqual(1, neuron_log.count())
        #self.assertEqual(1, treenode_skeleton_relation.count())
        #neuron_log_location = neuron_log[0].location
        #self.assertEqual(5, neuron_log_location.x)
        #self.assertEqual(10, neuron_log_location.y)
        #self.assertEqual(15, neuron_log_location.z)

    def test_create_treenode_with_existing_neuron(self):
        self.fake_authentication()
        relation_map = get_relation_to_id_map(self.test_project_id)
        class_map = get_class_to_id_map(self.test_project_id)
        neuron_id = 2389
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_treenodes = lambda: Treenode.objects.all().count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()

        response = self.client.post('/%d/treenode/create' % self.test_project_id, {
            'x': 5,
            'y': 10,
            'z': 15,
            'confidence': 5,
            'parent_id': -1,
            'useneuron': neuron_id,
            'radius': 2})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        self.assertTrue('treenode_id' in parsed_response)
        self.assertTrue('skeleton_id' in parsed_response)

        self.assertEqual(treenode_count + 1, count_treenodes())
        self.assertEqual(skeleton_count + 1, count_skeletons())

        treenode_skeleton_relation = TreenodeClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['element_of'],
                treenode=parsed_response['treenode_id'],
                class_instance=parsed_response['skeleton_id'])
        neuron_skeleton_relation = ClassInstanceClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['model_of'],
                class_instance_a=parsed_response['skeleton_id'],
                class_instance_b=neuron_id)

        # FIXME: treenode_skeleton_relation.count() should be 1, but we
        # currently don't store these relations.
        # See: https://github.com/catmaid/CATMAID/issues/754
        self.assertEqual(0, treenode_skeleton_relation.count())
        self.assertEqual(1, neuron_skeleton_relation.count())

    def test_create_treenode_with_nonexisting_parent_failure(self):
        self.fake_authentication()
        parent_id = 555555
        treenode_count = Treenode.objects.all().count()
        relation_count = TreenodeClassInstance.objects.all().count()
        response = self.client.post('/%d/treenode/create' % self.test_project_id, {
            'x': 5,
            'y': 10,
            'z': 15,
            'confidence': 5,
            'parent_id': parent_id,
            'radius': 2})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'error': 'No skeleton and neuron for treenode %d' % parent_id}
        self.assertIn(expected_result['error'], parsed_response['error'])
        self.assertEqual(treenode_count, Treenode.objects.all().count())
        self.assertEqual(relation_count, TreenodeClassInstance.objects.all().count())

    def test_delete_root_treenode_with_children_failure(self):
        self.fake_authentication()
        treenode_id = 367

        tn_count = Treenode.objects.all().count()
        child_count = Treenode.objects.filter(parent=treenode_id).count()
        response = self.client.post(
                '/%d/treenode/delete' % self.test_project_id,
                {'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = "Could not delete root node: You can't delete the " \
                          "root node when it has children."
        self.assertEqual(expected_result, parsed_response['error'])
        self.assertEqual(1, Treenode.objects.filter(id=treenode_id).count())
        self.assertEqual(tn_count, Treenode.objects.all().count())
        self.assertEqual(child_count, Treenode.objects.filter(parent=treenode_id).count())

    def test_insert_treenoded_on_edge(self):
        self.fake_authentication()
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        # Get two nodes and calculate point between them
        child_id = 2374
        parent_id = 2372
        child = Treenode.objects.get(pk=child_id)
        parent = Treenode.objects.get(pk=parent_id)

        new_node_x = 0.5 * (child.location_x + parent.location_x)
        new_node_y = 0.5 * (child.location_y + parent.location_y)
        new_node_z = 0.5 * (child.location_z + parent.location_z)

        response = self.client.post('/%d/treenode/insert' % self.test_project_id, {
            'x': new_node_x,
            'y': new_node_y,
            'z': new_node_z,
            'child_id': child_id,
            'parent_id': parent_id})

        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        self.assertTrue('treenode_id' in parsed_response)
        self.assertTrue('skeleton_id' in parsed_response)

        self.assertEqual(treenode_count + 1, count_treenodes())
        self.assertEqual(skeleton_count, count_skeletons())
        self.assertEqual(neuron_count, count_neurons())

        new_node_id = parsed_response['treenode_id']
        new_node = Treenode.objects.get(pk=new_node_id)
        child = Treenode.objects.get(pk=child_id)
        self.assertEqual(new_node.parent_id, parent_id)
        self.assertEqual(child.parent_id, new_node_id)
        self.assertEqual(new_node.user, child.user)
        self.assertEqual(new_node.creation_time, child.creation_time)
        self.assertEqual(new_node.skeleton_id, child.skeleton_id)
        self.assertEqual(new_node.location_x, new_node_x)
        self.assertEqual(new_node.location_y, new_node_y)
        self.assertEqual(new_node.location_z, new_node_z)

    def test_insert_treenoded_not_on_edge_with_permission(self):
        self.fake_authentication()
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        # Get two nodes and calculate point between them
        child_id = 2374
        parent_id = 2372
        child = Treenode.objects.get(pk=child_id)
        parent = Treenode.objects.get(pk=parent_id)

        new_node_x = 0.5 * (child.location_x + parent.location_x)
        new_node_y = 0.5 * (child.location_y + parent.location_y) + 10
        new_node_z = 0.5 * (child.location_z + parent.location_z)

        # Try to insert with a slight distorition in Y. This is allowed if the
        # user has permission to edit the neuron.
        response = self.client.post('/%d/treenode/insert' % self.test_project_id, {
            'x': new_node_x,
            'y': new_node_y,
            'z': new_node_z,
            'child_id': child_id,
            'parent_id': parent_id})

        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        self.assertTrue('treenode_id' in parsed_response)
        self.assertTrue('skeleton_id' in parsed_response)

        self.assertEqual(treenode_count + 1, count_treenodes())
        self.assertEqual(skeleton_count, count_skeletons())
        self.assertEqual(neuron_count, count_neurons())

        new_node_id = parsed_response['treenode_id']
        new_node = Treenode.objects.get(pk=new_node_id)
        child = Treenode.objects.get(pk=child_id)
        self.assertEqual(new_node.parent_id, parent_id)
        self.assertEqual(child.parent_id, new_node_id)
        self.assertEqual(new_node.user, child.user)
        self.assertEqual(new_node.creation_time, child.creation_time)
        self.assertEqual(new_node.skeleton_id, child.skeleton_id)
        self.assertEqual(new_node.location_x, new_node_x)
        self.assertEqual(new_node.location_y, new_node_y)
        self.assertEqual(new_node.location_z, new_node_z)

    def test_insert_treenoded_not_on_edge_without_permission(self):
        self.fake_authentication(username='test0')
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        # Get two nodes and calculate point between them
        child_id = 2374
        parent_id = 2372
        child = Treenode.objects.get(pk=child_id)
        parent = Treenode.objects.get(pk=parent_id)

        # Set chld and parent to different creators and lock it
        owner = User.objects.get(username='admin')
        for n in (child, parent):
            n.creator = owner
            n.save()

        new_node_x = 0.5 * (child.location_x + parent.location_x)
        new_node_y = 0.5 * (child.location_y + parent.location_y) + 10
        new_node_z = 0.5 * (child.location_z + parent.location_z)

        # Try to insert with a slight distorition in Y. This should fail since
        # the new node would introduce a structural change to the skeleton.
        response = self.client.post('/%d/treenode/insert' % self.test_project_id, {
            'x': new_node_x,
            'y': new_node_y,
            'z': new_node_z,
            'child_id': child_id,
            'parent_id': parent_id})

        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue('error' in parsed_response)

        self.assertEqual(treenode_count, count_treenodes())
        self.assertEqual(skeleton_count, count_skeletons())
        self.assertEqual(neuron_count, count_neurons())

    def test_insert_treenoded_no_child_parent(self):
        self.fake_authentication()
        class_map = get_class_to_id_map(self.test_project_id)
        count_treenodes = lambda: Treenode.objects.all().count()
        count_skeletons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['skeleton']).count()
        count_neurons = lambda: ClassInstance.objects.filter(
                project=self.test_project_id,
                class_column=class_map['neuron']).count()

        treenode_count = count_treenodes()
        skeleton_count = count_skeletons()
        neuron_count = count_neurons()

        # Get two nodes and calculate point between them
        child_id = 2376
        parent_id = 2372
        child = Treenode.objects.get(pk=child_id)
        parent = Treenode.objects.get(pk=parent_id)

        new_node_x = 0.5 * (child.location_x + parent.location_x)
        new_node_y = 0.5 * (child.location_y + parent.location_y)
        new_node_z = 0.5 * (child.location_z + parent.location_z)

        # Try to insert with a slight distorition in Y
        response = self.client.post('/%d/treenode/insert' % self.test_project_id, {
            'x': new_node_x,
            'y': new_node_y,
            'z': new_node_z,
            'child_id': child_id,
            'parent_id': parent_id})

        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertTrue('error' in parsed_response)

        self.assertEqual(treenode_count, count_treenodes())
        self.assertEqual(skeleton_count, count_skeletons())
        self.assertEqual(neuron_count, count_neurons())

    def test_delete_non_root_non_parent_treenode(self):
        self.fake_authentication()
        treenode_id = 349

        tn_count = Treenode.objects.all().count()
        response = self.client.post(
                '/%d/treenode/delete' % self.test_project_id,
                {'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = 'Removed treenode successfully.'
        self.assertEqual(expected_result, parsed_response['success'])
        self.assertEqual(0, Treenode.objects.filter(id=treenode_id).count())
        self.assertEqual(tn_count - 1, Treenode.objects.all().count())

    def test_delete_root_treenode(self):
        self.fake_authentication()
        treenode_id = 2437

        treenode = Treenode.objects.filter(id=treenode_id)[0]
        children = Treenode.objects.filter(parent=treenode_id)
        self.assertEqual(0, children.count())
        self.assertEqual(None, treenode.parent)
        tn_count = Treenode.objects.all().count()

        response = self.client.post(
                '/%d/treenode/delete' % self.test_project_id,
                {'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
            'success': 'Removed treenode successfully.',
            'parent_id': None,
            'deleted_neuron': True,
            'skeleton_id': 2433
        }
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(0, Treenode.objects.filter(id=treenode_id).count())
        self.assertEqual(tn_count - 1, Treenode.objects.all().count())

    def test_delete_non_root_treenode(self):
        self.fake_authentication()
        treenode_id = 265

        relation_map = get_relation_to_id_map(self.test_project_id)
        get_skeleton = lambda: TreenodeClassInstance.objects.filter(
                project=self.test_project_id,
                relation=relation_map['element_of'],
                treenode=treenode_id)
        self.assertEqual(1, get_skeleton().count())

        children = Treenode.objects.filter(parent=treenode_id)
        self.assertTrue(children.count() > 0)
        tn_count = Treenode.objects.all().count()
        parent = get_object_or_404(Treenode, id=treenode_id).parent

        response = self.client.post(
                '/%d/treenode/delete' % self.test_project_id,
                {'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = 'Removed treenode successfully.'
        self.assertEqual(expected_result, parsed_response['success'])
        self.assertEqual(0, Treenode.objects.filter(id=treenode_id).count())
        self.assertEqual(0, get_skeleton().count())
        self.assertEqual(tn_count - 1, Treenode.objects.all().count())

        for child in children:
            child_after_change = get_object_or_404(Treenode, id=child.id)
            self.assertEqual(parent, child_after_change.parent)

    def test_search_with_no_nodes(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'tr'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"id":374, "name":"downstream-A", "class_name":"neuron"},
                {"id":362, "name":"downstream-B", "class_name":"neuron"}]
        self.assertEqual(expected_result, parsed_response)

    def test_search_with_no_results(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'bobobobobobobo'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []
        self.assertEqual(expected_result, parsed_response)

    def test_search_with_several_nodes(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 't'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"id":465, "name":"tubby bye bye", "class_name":"driver_line"},
                {"id":4, "name":"Fragments", "class_name":"group"},
                {"id":364, "name":"Isolated synaptic terminals", "class_name":"group"},
                {"id":2353, "name":"synapse with more targets", "class_name":"label"},
                {"id":2345, "name":"t", "class_name":"label"},
                {"id":351, "name":"TODO", "class_name":"label", "nodes":[
                    {"id":349, "x":3580, "y":3350, "z":252, "skid":1},
                    {"id":261, "x":2820, "y":1345, "z":0, "skid":235}]},
                {"id":2342, "name":"uncertain end", "class_name":"label", "nodes":[
                    {"id":403, "x":7840, "y":2380, "z":0, "skid":373}]},
                {"id":374, "name":"downstream-A", "class_name":"neuron"},
                {"id":362, "name":"downstream-B", "class_name":"neuron"},
                {"id":1, "name":"dull skeleton", "class_name":"skeleton"},
                {"id":235, "name":"skeleton 235", "class_name":"skeleton"},
                {"id":2364, "name":"skeleton 2364", "class_name":"skeleton"},
                {"id":2388, "name":"skeleton 2388", "class_name":"skeleton"},
                {"id":2411, "name":"skeleton 2411", "class_name":"skeleton"},
                {"id":2433, "name":"skeleton 2433", "class_name":"skeleton"},
                {"id":2440, "name":"skeleton 2440", "class_name":"skeleton"},
                {"id":2451, "name":"skeleton 2451", "class_name":"skeleton"},
                {"id":361, "name":"skeleton 361", "class_name":"skeleton"},
                {"id":373, "name":"skeleton 373", "class_name":"skeleton"}]
        self.assertEqual(expected_result, parsed_response)

    def test_search_with_nodes_and_nonode_label(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'a'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"id":485, "name":"Local", "class_name":"cell_body_location"},
                {"id":487, "name":"Non-Local", "class_name":"cell_body_location"},
                {"id":454, "name":"and", "class_name":"driver_line"},
                {"id":4, "name":"Fragments", "class_name":"group"},
                {"id":364, "name":"Isolated synaptic terminals", "class_name":"group"},
                {"id":2353, "name":"synapse with more targets", "class_name":"label"},
                {"id":2342, "name":"uncertain end", "class_name":"label", "nodes":[
                    {"id":403, "x":7840, "y":2380, "z":0, "skid":373}]},
                {"id":233, "name":"branched neuron", "class_name":"neuron"},
                {"id":374, "name":"downstream-A", "class_name":"neuron"},
                {"id":362, "name":"downstream-B", "class_name":"neuron"}]
        self.assertEqual(expected_result, parsed_response)

    def test_search_with_nodes_and_duplicate_label(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'uncertain end'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        # Expect only one result that has a node linked
        expected_result = [
            {"id":2342, "name":"uncertain end", "class_name":"label", "nodes":[
                {"id":403, "x":7840, "y":2380, "z":0, "skid":373}]},
        ]
        self.assertItemsEqual(expected_result, parsed_response)

        # Add a duplicate record of the label, without any node links
        label = ClassInstance.objects.get(id=2342)
        label.id = None
        label.save()

        response2 = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'uncertain end'})
        self.assertEqual(response2.status_code, 200)
        parsed_response2 = json.loads(response2.content)

        # Expect the nodes to be not linked to the duplicate record
        expected_result2 = [
            {"id":label.id, "name":"uncertain end", "class_name":"label"},
            {"id":2342, "name":"uncertain end", "class_name":"label", "nodes":[
                {"id":403, "x":7840, "y":2380, "z":0, "skid":373}]}
        ]
        self.assertItemsEqual(expected_result2, parsed_response2)

    def test_search_with_nodes(self):
        self.fake_authentication()

        response = self.client.get(
                '/%d/search' % self.test_project_id,
                {'substring': 'c'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"id":485, "name":"Local", "class_name":"cell_body_location"},
                {"id":487, "name":"Non-Local", "class_name":"cell_body_location"},
                {"id":458, "name":"c005", "class_name":"driver_line"},
                {"id":364, "name":"Isolated synaptic terminals", "class_name":"group"},
                {"id":2342, "name":"uncertain end", "class_name":"label",
                    "nodes":[{"id":403, "x":7840, "y":2380, "z":0, "skid":373}]},
                {"id":233, "name":"branched neuron", "class_name":"neuron"}]
        self.assertEqual(expected_result, parsed_response)

    def test_delete_link_success(self):
        self.fake_authentication()
        connector_id = 356
        treenode_id = 377

        tc_count = TreenodeConnector.objects.all().count()
        response = self.client.post(
                '/%d/link/delete' % self.test_project_id,
                {'connector_id': connector_id, 'treenode_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'result': 'Removed treenode to connector link'}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(0, TreenodeConnector.objects.filter(connector=connector_id, treenode=treenode_id).count())
        self.assertEqual(tc_count - 1, TreenodeConnector.objects.all().count())

    def test_reroot_skeleton(self):
        self.fake_authentication()

        new_root = 407

        count_logs = lambda: Log.objects.all().count()
        log_count = count_logs()

        response = self.client.post(
                '/%d/skeleton/reroot' % self.test_project_id,
                {'treenode_id': new_root})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'newroot': 407}
        self.assertEqual(expected_result, parsed_response)
        self.assertEqual(1 + log_count, count_logs())

        def assertHasParent(treenode_id, parent_id):
            treenode = get_object_or_404(Treenode, id=treenode_id)
            self.assertEqual(parent_id, treenode.parent_id)

        assertHasParent(405, 407)
        assertHasParent(377, 405)
        assertHasParent(407, None)

    def assertTreenodeHasProperties(self, treenode_id, parent_id, skeleton_id):
        treenode = get_object_or_404(Treenode, id=treenode_id)
        self.assertEqual(parent_id, treenode.parent_id)
        self.assertEqual(skeleton_id, treenode.skeleton_id)

    def test_reroot_and_join_skeletons(self):
        self.fake_authentication()

        new_root = 2394
        link_to = 2394 # Skeleton ID: 2388
        link_from = 2415 # Skeleton ID: 2411

        count_logs = lambda: Log.objects.all().count()
        log_count = count_logs()
        new_skeleton_id = get_object_or_404(Treenode, id=link_from).skeleton_id

        response = self.client.post(
                '/%d/skeleton/reroot' % self.test_project_id,
                {'treenode_id': new_root})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'newroot': 2394}
        self.assertEqual(expected_result, parsed_response)

        response = self.client.post(
                '/%d/skeleton/join' % self.test_project_id, {
                    'from_id': link_from,
                    'to_id': link_to,
                    'annotation_set': '{}'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                'message': 'success',
                'fromid': link_from,
                'toid': link_to}
        self.assertEqual(expected_result, parsed_response)

        self.assertEqual(2 + log_count, count_logs())

        self.assertTreenodeHasProperties(2396, 2394, new_skeleton_id)
        self.assertTreenodeHasProperties(2392, 2394, new_skeleton_id)
        self.assertTreenodeHasProperties(2394, 2415, new_skeleton_id)

        self.assertEqual(0, ClassInstance.objects.filter(id=2388).count())
        self.assertEqual(0, ClassInstanceClassInstance.objects.filter(id=2390).count())

        self.assertEqual(new_skeleton_id, get_object_or_404(TreenodeConnector, id=2405).skeleton_id)

    def test_skeleton_contributor_statistics(self):
        self.fake_authentication()

        response = self.client.post(
            '/%d/skeleton/contributor_statistics_multiple' % (self.test_project_id,),
            {'skids[0]': 235})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_response = {
                "pre_contributors": {"3": 3},
                "multiuser_review_minutes": 0,
                "node_contributors": {"3": 28},
                "construction_minutes": 1,
                "n_nodes": 28,
                "min_review_minutes": 0,
                "n_pre": 3,
                "post_contributors": {},
                "n_post": 0}
        self.assertEqual(parsed_response, expected_response)

        response = self.client.post(
            '/%d/skeleton/%d/contributor_statistics' % (self.test_project_id, 235))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(parsed_response, expected_response)

        response = self.client.post(
            '/%d/skeleton/contributor_statistics_multiple' % (self.test_project_id,),
            {'skids[0]': 235, 'skids[1]': 361})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_response = {
                "pre_contributors": {"3": 3},
                "multiuser_review_minutes": 0,
                "node_contributors": {"3": 37},
                "construction_minutes": 1,
                "n_nodes": 37,
                "min_review_minutes": 0,
                "n_pre": 3,
                "post_contributors": {"3": 1},
                "n_post": 1}
        self.assertEqual(parsed_response, expected_response)

    def test_split_skeleton(self):
        self.fake_authentication()

        # Test simple split of 3-node skeleton at middle node.
        old_skeleton_id = 2388
        response = self.client.post(
            '/%d/skeleton/split' % (self.test_project_id,),
            {'treenode_id': 2394, 'upstream_annotation_map': '{}', 'downstream_annotation_map': '{}'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        new_skeleton_id = parsed_response['skeleton_id']

        self.assertTreenodeHasProperties(2392, None, old_skeleton_id)
        self.assertTreenodeHasProperties(2394, None, new_skeleton_id)
        self.assertTreenodeHasProperties(2396, 2394, new_skeleton_id)

        # Test error is returned when trying to split root node.
        response = self.client.post(
            '/%d/skeleton/split' % (self.test_project_id,),
            {'treenode_id': 237, 'upstream_annotation_map': '{}', 'downstream_annotation_map': '{}'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                "error": "Can't split at the root node: it doesn't have a parent."}
        self.assertEqual(expected_result, parsed_response)

    def test_split_skeleton_annotations(self):
        self.fake_authentication()

        # Annotate skeleton with three test annotations.
        old_skeleton_id = 2388
        response = self.client.post(
            '/%d/annotations/add' % (self.test_project_id,),
            {'annotations[0]': 'A',
             'annotations[1]': 'B',
             'annotations[2]': 'C',
             'skeleton_ids[0]': old_skeleton_id})
        self.assertEqual(response.status_code, 200)

        # Expect an error if some annotations are not assigned.
        response = self.client.post(
            '/%d/skeleton/split' % (self.test_project_id,),
            {'treenode_id': 2394,
             'upstream_annotation_map':   json.dumps({'A': self.test_user_id}),
             'downstream_annotation_map': json.dumps({'C': self.test_user_id})})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = "Annotation distribution is not valid for splitting. " \
                          "One part has to keep the whole set of annotations!"
        self.assertEqual(expected_result, parsed_response['error'])

        # Expect an error if all annotations are assigned, but neither part has
        # all.
        response = self.client.post(
            '/%d/skeleton/split' % (self.test_project_id,),
            {'treenode_id': 2394,
             'upstream_annotation_map':   json.dumps({'A': self.test_user_id, 'B': self.test_user_id}),
             'downstream_annotation_map': json.dumps({'C': self.test_user_id, 'B': self.test_user_id})})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = "Annotation distribution is not valid for splitting. " \
                          "One part has to keep the whole set of annotations!"
        self.assertEqual(expected_result, parsed_response['error'])

        # Test correct assignment of annotations in normal case, including
        # removal of annotation from skeleton retaining original ID.
        response = self.client.post(
            '/%d/skeleton/split' % (self.test_project_id,),
            {'treenode_id': 2394,
             'upstream_annotation_map':   json.dumps({'A': self.test_user_id, 'B': self.test_user_id}),
             'downstream_annotation_map': json.dumps({'A': self.test_user_id, 'B': self.test_user_id, 'C': self.test_user_id})})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        new_skeleton_id = parsed_response['skeleton_id']

        response = self.client.post(
            '/%d/skeleton/annotationlist' % (self.test_project_id,),
            {'skeleton_ids[0]': old_skeleton_id,
             'skeleton_ids[1]': new_skeleton_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        old_skeleton_annotations = set([parsed_response['annotations'][str(aid['id'])] for aid in parsed_response['skeletons'][str(old_skeleton_id)]['annotations']])
        new_skeleton_annotations = set([parsed_response['annotations'][str(aid['id'])] for aid in parsed_response['skeletons'][str(new_skeleton_id)]['annotations']])
        self.assertEqual(old_skeleton_annotations, set(['A', 'B']))
        self.assertEqual(new_skeleton_annotations, set(['A', 'B', 'C']))

    def test_skeleton_connectivity(self):
        self.fake_authentication()

        # Test a simple request like that from the connectivity widget.
        response = self.client.post(
            '/%d/skeletons/connectivity' % (self.test_project_id,),
            {'source_skeleton_ids[0]': 235,
             'source_skeleton_ids[1]': 373,
             'boolean_op': 'OR'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
            "outgoing_reviewers": [],
            "outgoing": {"361": {"skids": {"235": [0, 0, 0, 0, 1]}, "num_nodes": 9},
                         "373": {"skids": {"235": [0, 0, 0, 0, 2]}, "num_nodes": 5}},
            "incoming": {"235": {"skids": {"373": [0, 0, 0, 0, 2]}, "num_nodes": 28}},
            "incoming_reviewers": []}
        self.assertEqual(expected_result, parsed_response)

        # Test for conjunctive connectivity.
        response = self.client.post(
            '/%d/skeletons/connectivity' % (self.test_project_id,),
            {'source_skeleton_ids[0]': 235,
             'source_skeleton_ids[1]': 373,
             'boolean_op': 'AND'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
            "outgoing_reviewers": [],
            "outgoing": {},
            "incoming": {},
            "incoming_reviewers": []}
        self.assertEqual(expected_result, parsed_response)

    def test_treenode_info_nonexisting_treenode_failure(self):
        self.fake_authentication()
        treenode_id = 55555

        response = self.client.post(
                '/%d/treenodes/%s/info' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = 'No skeleton and neuron for treenode %s' % treenode_id
        self.assertIn('error', parsed_response)
        self.assertEqual(expected_result, parsed_response['error'])

    def test_treenode_info(self):
        self.fake_authentication()
        treenode_id = 239

        response = self.client.post(
                '/%d/treenodes/%s/info' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'skeleton_id': 235, 'neuron_id': 233, 'skeleton_name': 'skeleton 235', 'neuron_name': 'branched neuron'}
        self.assertEqual(expected_result, parsed_response)

    def assertTreenodeHasRadius(self, treenode_id, radius):
        """Helper function for radius update tests."""
        treenode = Treenode.objects.get(id=treenode_id)
        self.assertEqual(radius, treenode.radius,
                'Treenode %d has radius %s not %s' % (treenode_id, treenode.radius, radius))

    def test_update_treenode_radius_single_node(self):
        self.fake_authentication()

        treenode_id = 257
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 0})
        self.assertEqual(response.status_code, 200)

        expected = [(259, old_r), (257, new_r), (255, old_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_update_treenode_radius_next_branch(self):
        self.fake_authentication()

        # Test to end node
        treenode_id = 257
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 1})
        self.assertEqual(response.status_code, 200)

        expected = [(261, new_r), (259, new_r), (257, new_r),
                    (255, old_r), (253, old_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

        # Test to branch node
        treenode_id = 263
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 1})
        self.assertEqual(response.status_code, 200)

        expected = [(253, old_r), (263, new_r), (265, new_r),
                    (269, old_r), (267, old_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_update_treenode_radius_prev_branch(self):
        self.fake_authentication()

        # Test to branch node
        treenode_id = 257
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 2})
        self.assertEqual(response.status_code, 200)

        expected = [(261, old_r), (259, old_r), (257, new_r),
                    (255, new_r), (253, old_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

        # Test to root node
        treenode_id = 253
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 2})
        self.assertEqual(response.status_code, 200)

        expected = [(255, new_r), (263, old_r), (253, new_r),
                    (251, new_r), (249, new_r), (247, new_r),
                    (247, new_r), (245, new_r), (243, new_r),
                    (241, new_r), (239, new_r), (237, old_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_update_treenode_radius_prev_defined_node(self):
        self.fake_authentication()

        # Set radius at ancestor node
        ancestor = Treenode.objects.get(id=251)
        ancestor.radius = 7
        ancestor.save()

        # Test to previous defined node
        treenode_id = 257
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 3})
        self.assertEqual(response.status_code, 200)

        expected = [(261, old_r), (259, old_r), (257, new_r),
                    (255, new_r), (253, new_r), (251, 7)]

        # Test on node with defined radius (and propagation to root)
        treenode_id = ancestor.id
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 3})
        self.assertEqual(response.status_code, 200)

        expected = [(253, new_r), (251, new_r), (249, new_r),
                    (247, new_r), (247, new_r), (245, new_r),
                    (243, new_r), (241, new_r), (239, new_r),
                    (237, new_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_update_treenode_radius_to_root(self):
        self.fake_authentication()

        treenode_id = 257
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 4})
        self.assertEqual(response.status_code, 200)

        expected = [(261, old_r), (259, old_r), (257, new_r),
                    (255, new_r), (253, new_r), (263, old_r),
                    (251, new_r), (249, new_r), (247, new_r),
                    (247, new_r), (245, new_r), (243, new_r),
                    (241, new_r), (239, new_r), (237, new_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_update_treenode_radius_all_nodes(self):
        self.fake_authentication()

        treenode_id = 2417
        new_r = 5
        old_r = -1
        response = self.client.post(
                '/%d/treenode/%d/radius' % (self.test_project_id, treenode_id),
                {'radius': new_r, 'option': 5})
        self.assertEqual(response.status_code, 200)

        expected = [(2419, new_r), (2417, new_r), (2415, new_r), (2423, new_r)]
        for x in expected:
            self.assertTreenodeHasRadius(*x)

    def test_remove_annotations(self):
        self.fake_authentication()
        skeleton_id = 2364
        neuron_id = 2365

        # Annotate skeleton with three test annotations.
        response = self.client.post(
            '/%d/annotations/add' % (self.test_project_id,),
            {'annotations[0]': 'A',
             'annotations[1]': 'B',
             'annotations[2]': 'C',
             'skeleton_ids[0]': skeleton_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        annotations = {a['name']:a['id'] for a in parsed_response['annotations']}
        for a in ('A', 'B', 'C'):
            self.assertTrue(a in annotations)

        # Remove annotations A and C and expect B to still be there
        response = self.client.post(
            '/%d/annotations/remove' % (self.test_project_id,),
            {'entity_ids[0]': neuron_id,
             'annotation_ids[0]': annotations['A'],
             'annotation_ids[1]': annotations['C']})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        response = self.client.post(
            '/%d/annotations/forskeletons' % (self.test_project_id,),
            {'skeleton_ids[0]': skeleton_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        linked_annotations = parsed_response['skeletons'][str(skeleton_id)]
        linked_annotation_ids = [a['id'] for a in linked_annotations]
        self.assertFalse(annotations['A'] in linked_annotation_ids)
        self.assertFalse(annotations['C'] in linked_annotation_ids)


    def test_read_message_error(self):
        self.fake_authentication()
        message_id = 5050

        response = self.client.get('/messages/mark_read', {'id': message_id})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Could not retrieve message with id %s' % message_id)

    def test_read_message_without_action(self):
        self.fake_authentication()
        message_id = 3

        response = self.client.get('/messages/mark_read', {'id': message_id})
        self.assertEqual(response.status_code, 200)
        message = Message.objects.get(id=message_id)
        self.assertEqual(True, message.read)
        self.assertContains(response, 'history.back()', count=2)

    def test_read_message_with_action(self):
        self.fake_authentication()
        message_id = 1

        response = self.client.get('/messages/mark_read', {'id': message_id})
        self.assertEqual(response.status_code, 200)
        message = Message.objects.filter(id=message_id)[0]
        self.assertEqual(True, message.read)
        self.assertContains(response, 'location.replace')
        self.assertContains(response, message.action, count=2)

    def test_list_messages(self):
        self.fake_authentication()

        response = self.client.post(
                '/messages/list', {})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)

        def get_message(data, id):
            msgs = [d for d in data if d['id'] == id]
            if len(msgs) != 1:
                raise ValueError("Malformed message data")
            return msgs[0]

        expected_result = {
                '0': {
                    'action': '',
                    'id': 3,
                    'text': 'Contents of message 3.',
                    'time': '2014-10-05 11:12:01.360422+00:00',
                    'title': 'Message 3'
                },
                '1': {
                    'action': 'http://www.example.com/message2',
                    'id': 2,
                    'text': 'Contents of message 2.',
                    'time': '2011-12-20 16:46:01.360422+00:00',
                    'title': 'Message 2'
                },
                '2': {
                    'action': 'http://www.example.com/message1',
                    'id': 1,
                    'text': 'Contents of message 1.',
                    'time': '2011-12-19 16:46:01+00:00',
                    'title': 'Message 1'
                },
                '3': {
                    'id': -1,
                    'notification_count': 0
                }
        }
        # Check result independent from order
        for mi in ('0','1','2','3'):
            self.assertEqual(expected_result[mi], parsed_response[mi])

    def test_skeleton_open_leaves(self):
        skeleton_id = 235

        self.fake_authentication()
        url = '/%d/skeletons/%d/open-leaves' % (self.test_project_id, skeleton_id,)

        # Return untagged root
        response = self.client.post(url, {'treenode_id': 243})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        distsort = lambda end: end[2]
        parsed_response.sort(key=distsort)
        expected_result = \
                [[237, [1065.0, 3035.0, 0.0],  4, u'2011-09-27T07:49:15.802Z'],
                 [261, [2820.0, 1345.0, 0.0], 10, u'2011-09-27T07:49:25.549Z'],
                 [277, [6090.0, 1550.0, 0.0], 13, u'2011-09-27T07:49:33.770Z'],
                 [417, [4990.0, 4200.0, 0.0], 16, u'2011-10-07T07:02:15.176Z']]
        self.assertEqual(parsed_response, expected_result)

        # Tag soma and try again
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, 237),
                {'tags': 'soma', 'delete_existing': 'false'})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'treenode_id': 243})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        parsed_response.sort(key=distsort)
        expected_result.pop(0)
        self.assertEqual(parsed_response, expected_result)

        # Tag branch and try again, should be shortest path (277) not nearest (417)
        # Also check tag case insensitivity.
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, 261),
                {'tags': 'End', 'delete_existing': 'false'})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'treenode_id': 243})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        parsed_response.sort(key=distsort)
        expected_result.pop(0)
        self.assertEqual(parsed_response, expected_result)

        # Check that an arbitrary tag containing 'end' is still considered open.
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, 277),
                {'tags': 'mitochondria ends', 'delete_existing': 'false'})
        self.assertEqual(response.status_code, 200)
        response = self.client.post(url, {'treenode_id': 243})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        parsed_response.sort(key=distsort)
        self.assertEqual(parsed_response, expected_result)

    def test_skeleton_find_labels(self):
        self.fake_authentication()

        # Create labels.
        treenode_id = 387
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, treenode_id),
                {'tags': 'testlabel'})
        self.assertEqual(response.status_code, 200)
        treenode_id = 393
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, treenode_id),
                {'tags': 'Testlabel'})
        self.assertEqual(response.status_code, 200)
        # Label in other skeleton than should be ignored.
        treenode_id = 403
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, treenode_id),
                {'tags': 'Testlabel'})
        self.assertEqual(response.status_code, 200)

        skeleton_id = 361
        treenode_id = 367
        response = self.client.post(
                '/%d/skeletons/%d/find-labels' % (self.test_project_id, skeleton_id),
                {'treenode_id': treenode_id,
                 'label_regex': '[Tt]estlabel'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[393, [6910.0, 990.0, 0.0], 3, ["Testlabel"]],
                           [387, [9030.0, 1480.0, 0.0], 4, ["testlabel"]]]
        self.assertEqual(expected_result, parsed_response)

    def test_skeleton_ancestry(self):
        skeleton_id = 361

        self.fake_authentication()
        response = self.client.post(
                '/%d/skeleton/ancestry' % self.test_project_id,
                {'skeleton_id': skeleton_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"name":"downstream-B", "id":362, "class":"neuron"},
                {"name":"Isolated synaptic terminals", "id":364, "class":"group"},
                {"name":"neuropile", "id":2323, "class":"root"}]
        self.assertEqual(expected_result, parsed_response)

    def test_skeleton_ancestry_2(self):
        skeleton_id = 2364

        self.fake_authentication()
        response = self.client.post(
                '/%d/skeleton/ancestry' % self.test_project_id,
                {'skeleton_id': skeleton_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [
                {"name":"neuron 2365", "id":2365, "class":"neuron"},
                {"name":"Fragments", "id":4, "class":"group"},
                {"name":"neuropile", "id":2323, "class":"root"}]
        self.assertEqual(expected_result, parsed_response)

    def test_create_postsynaptic_link_success(self):
        from_id = 237
        to_id = 432
        link_type = 'postsynaptic_to'
        self.fake_authentication()
        response = self.client.post(
                '/%d/link/create' % self.test_project_id,
                {
                    'from_id': from_id,
                    'to_id': to_id,
                    'link_type': link_type
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)

    def test_create_presynaptic_link_fail_due_to_other_presynaptic_links(self):
        from_id = 237
        to_id = 432
        link_type = 'presynaptic_to'
        self.fake_authentication()
        response = self.client.post(
                '/%d/link/create' % self.test_project_id,
                {
                    'from_id': from_id,
                    'to_id': to_id,
                    'link_type': link_type
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'error': 'Connector %s does not have zero presynaptic connections.' % to_id}
        self.assertEqual(expected_result, parsed_response)

    def test_create_presynaptic_link_success(self):
        from_id = 237
        to_id = 2458
        link_type = 'presynaptic_to'
        self.fake_authentication()
        response = self.client.post(
                '/%d/link/create' % self.test_project_id,
                {
                    'from_id': from_id,
                    'to_id': to_id,
                    'link_type': link_type
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'message': 'success'}
        self.assertEqual(expected_result, parsed_response)

    def test_node_nearest_for_skeleton(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/node/nearest' % self.test_project_id,
                {
                    'x': 5115,
                    'y': 3835,
                    'z': 4050,
                    'skeleton_id': 2388,
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                "treenode_id": 2394,
                "x": 3110,
                "y": 6030,
                "z": 0,
                "skeleton_id": 2388}
        self.assertEqual(expected_result, parsed_response)

    def test_node_nearest_for_neuron(self):
        self.fake_authentication()
        response = self.client.post(
                '/%d/node/nearest' % self.test_project_id,
                {
                    'x': 5115,
                    'y': 3835,
                    'z': 0,
                    'neuron_id': 362,
                    })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {
                "treenode_id": 367,
                "x": 7030,
                "y": 1980,
                "z": 0,
                "skeleton_id": 361}
        self.assertEqual(expected_result, parsed_response)

    def test_node_find_previous_branch(self):
        self.fake_authentication()
        treenode_id = 257

        response = self.client.post(
                '/%d/treenodes/%d/previous-branch-or-root' % (self.test_project_id, treenode_id),
                {'alt': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        # Response should contain one branch.
        expected_result = [253, 3685.0, 2160.0, 0.0]
        self.assertEqual(expected_result, parsed_response)

        treenode_id = 253
        response = self.client.post(
                '/%d/treenodes/%d/previous-branch-or-root' % (self.test_project_id, treenode_id),
                {'alt': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        # Response should contain one branch.
        expected_result = [237, 1065.0, 3035.0, 0.0]
        self.assertEqual(expected_result, parsed_response)

        treenode_id = 237
        response = self.client.post(
                '/%d/treenodes/%d/previous-branch-or-root' % (self.test_project_id, treenode_id),
                {'alt': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        # Response should contain one branch.
        expected_result = [237, 1065.0, 3035.0, 0.0]
        self.assertEqual(expected_result, parsed_response)

    def test_node_find_next_branch(self):
        self.fake_authentication()

        treenode_id = 391
        response = self.client.post(
                '/%d/treenodes/%d/next-branch-or-end' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        # Response should contain one branch.
        expected_result = [[[393, 6910.0, 990.0, 0.0],
                            [393, 6910.0, 990.0, 0.0],
                            [399, 5670.0, 640.0, 0.0]]]
        self.assertEqual(expected_result, parsed_response)

        treenode_id = 253
        response = self.client.post(
                '/%d/treenodes/%d/next-branch-or-end' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        # Response should contain two branches, and the larger branch headed by
        # node 263 should be first.
        expected_result = [[[263, 3915.0, 2105.0, 0.0],
                            [263, 3915.0, 2105.0, 0.0],
                            [265, 4570.0, 2125.0, 0.0]],
                           [[255, 3850.0, 1790.0, 0.0],
                            [255, 3850.0, 1790.0, 0.0],
                            [261, 2820.0, 1345.0, 0.0]]]
        self.assertEqual(expected_result, parsed_response)

    def test_treenode_find_children(self):
        self.fake_authentication()

        treenode_id = 387
        response = self.client.post(
                '/%d/treenodes/%d/children' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []
        self.assertEqual(expected_result, parsed_response)

        treenode_id = 385
        response = self.client.post(
                '/%d/treenodes/%d/children' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[[387, 9030.0, 1480.0, 0.0]]]
        self.assertEqual(expected_result, parsed_response)

        treenode_id = 367
        response = self.client.post(
                '/%d/treenodes/%d/children' % (self.test_project_id, treenode_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[383, 7850.0, 1970.0, 0.0], [391, 6740.0, 1530.0, 0.0]]
        parsed_response = [p[0] for p in parsed_response]
        for (expected, parsed) in zip(sorted(expected_result), sorted(parsed_response)):
             self.assertEqual(expected, parsed)

    def test_node_user_info(self):
        self.fake_authentication()

        treenode_id = 367
        response = self.client.post(
                '/%d/node/user-info' % (self.test_project_id),
                {'node_id': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {"reviewers": [],
                           "creation_time": "2011-09-27T07:57:17.808000+00:00",
                           "edition_time": "2011-12-05T13:51:36.955000+00:00",
                           "user": 3,
                           "review_times": [],
                           "editor": 3}
        self.assertEqual(expected_result, parsed_response)

    def test_node_get_location(self):
        self.fake_authentication()

        treenode_id = 383
        response = self.client.post(
                '/%d/node/get_location' % (self.test_project_id),
                {'tnid': treenode_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [383, 7850.0, 1970.0, 0.0]
        self.assertEqual(expected_result, parsed_response)

    def test_node_find_labels(self):
        self.fake_authentication()

        # Create labels.
        treenode_id = 387
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, treenode_id),
                {'tags': 'testlabel'})
        self.assertEqual(response.status_code, 200)
        treenode_id = 403
        response = self.client.post(
                '/%d/label/treenode/%d/update' % (self.test_project_id, treenode_id),
                {'tags': 'Testlabel'})
        self.assertEqual(response.status_code, 200)

        response = self.client.post(
                '/%d/nodes/find-labels' % (self.test_project_id, ),
                {'x': 8810,
                 'y': 1790,
                 'z': 0,
                 'label_regex': '[Tt]estlabel'})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = [[387, [9030.0, 1480.0, 0.0], 380.131556174964, ["testlabel"]],
                           [403, [7840.0, 2380.0, 0.0], 1135.3413583588, ["Testlabel"]]]
        self.assertEqual(expected_result, parsed_response)

    def test_node_update_single_treenode(self):
        self.fake_authentication()
        treenode_id = 289
        x = 5690
        y = 3340
        z = 0

        response = self.client.post(
                '/%d/node/update' % self.test_project_id, {
                    't[0][0]': treenode_id,
                    't[0][1]': x,
                    't[0][2]': y,
                    't[0][3]': z})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'updated': 1}
        self.assertEqual(expected_result, parsed_response)
        treenode = Treenode.objects.filter(id=treenode_id)[0]
        self.assertEqual(x, treenode.location_x)
        self.assertEqual(y, treenode.location_y)
        self.assertEqual(z, treenode.location_z)

    def test_node_update_invalid_location(self):
        self.fake_authentication()
        treenode_id = 289
        treenode = Treenode.objects.filter(id=treenode_id)[0]
        orig_x = treenode.location_x
        orig_y = treenode.location_y
        orig_z = treenode.location_z
        x = 5690
        z = 0

        for y in [float('NaN'), float('Infinity')]:
            response = self.client.post(
                    '/%d/node/update' % self.test_project_id, {
                        't[0][0]': treenode_id,
                        't[0][1]': x,
                        't[0][2]': y,
                        't[0][3]': z})
            self.assertEqual(response.status_code, 200)
            parsed_response = json.loads(response.content)
            self.assertIn('error', parsed_response)
            cursor = connection.cursor()
            cursor.execute('''
                SELECT location_x, location_y, location_z FROM location
                WHERE id=%s''' % treenode_id)
            treenode = cursor.fetchall()[0]
            self.assertEqual(orig_x, treenode[0])
            self.assertEqual(orig_y, treenode[1])
            self.assertEqual(orig_z, treenode[2])

    def test_node_update_many_nodes(self):
        self.fake_authentication()
        self.maxDiff = None
        node_id = [2368, 2370, 356, 421]
        x = [2990, 3060, 3640, 3850]
        y = [5200, 4460, 5060, 4800]
        z = [1, 2, 5, 6]
        types = ['t', 't', 'c', 'c']

        def insert_params(dictionary, param_id, params):
            """ Creates a parameter representation that is expected by the
            backend. Parameters are identified by a number: 0: id, 1: X, 2: Y
            and 3: Z. """
            for i,param in enumerate(params):
                dictionary['%s[%s][%s]' % (types[i], i, param_id)] = params[i]

        param_dict = {}
        insert_params(param_dict, 0, node_id)
        insert_params(param_dict, 1, x)
        insert_params(param_dict, 2, y)
        insert_params(param_dict, 3, z)

        response = self.client.post(
                '/%d/node/update' % self.test_project_id, param_dict)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'updated': 4}
        self.assertEqual(expected_result, parsed_response)
        i = 0
        for n_id in node_id:
            if types[i] == 't':
                node = Treenode.objects.filter(id=n_id)[0]
            else:
                node = Connector.objects.filter(id=n_id)[0]
            self.assertEqual(x[i], node.location_x)
            self.assertEqual(y[i], node.location_y)
            self.assertEqual(z[i], node.location_z)
            i += 1

    def test_node_no_update_many_nodes(self):
        self.fake_authentication()
        self.maxDiff = None
        node_id = [2368, 2370, 2372, 2374]
        x = [2990, 3060, 3210, 3460]
        y = [5200, 4460, 4990, 4830]
        z = [1, 2, 3, 4]
        types = ['t', 't', 't', 't']

        def insert_params(dictionary, param_id, params):
            """ Creates a parameter representation that is expected by the
            backend. Parameters are identified by a number: 0: id, 1: X, 2: Y
            and 3: Z. """
            for i,param in enumerate(params):
                dictionary['%s[%s][%s]' % (types[i], i, param_id)] = params[i]

        param_dict = {}
        insert_params(param_dict, 0, node_id)
        insert_params(param_dict, 1, x)
        insert_params(param_dict, 2, y)
        insert_params(param_dict, 3, z)

        response = self.client.post(
                '/%d/node/update' % self.test_project_id, param_dict)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {'error': 'User test2 cannot edit all of the 4 '
                           'unique objects from table treenode'}
        self.assertEqual(expected_result['error'], parsed_response['error'])

    def test_node_list_without_active_node(self):
        self.fake_authentication()
        expected_t_result = [
                [2372, 2370, 2760, 4600, 0, 5, -1, 2364, False],
                [2374, 2372, 3310, 5190, 0, 5, -1, 2364, False],
                [2376, 2374, 3930, 4330, 0, 5, -1, 2364, False],
                [2378, 2376, 4420, 4880, 0, 5, -1, 2364, False],
                [2394, 2392, 3110, 6030, 0, 5, -1, 2388, True],
                [2392, None, 2370, 6080, 0, 5, -1, 2388, True],
                [2396, 2394, 3680, 6550, 0, 5, -1, 2388, True],
                [2415, None, 4110, 6080, 0, 5, -1, 2411, True],
                [2417, 2415, 4400, 5730, 0, 5, -1, 2411, True],
                [2419, 2417, 5040, 5650, 0, 5, -1, 2411, True],
                [2423, 2415, 4140, 6460, 0, 5, -1, 2411, True],
        ]
        expected_c_result = [
                [2400, 3400, 5620, 0, 5, [[2394, 5], [2415, 5]], [[2374, 5]], [], True],
        ]
        response = self.client.post('/%d/node/list' % (self.test_project_id,), {
            'z1': 0,
            'top': 4625,
            'left': 2860,
            'right': 12625,
            'bottom': 8075,
            'z2': 9,
            'atnid': -1,
            'labels': False,
        })
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(len(expected_t_result), len(parsed_response[0]))
        self.assertEqual(len(expected_c_result), len(parsed_response[1]))
        for row in expected_t_result:
            self.assertTrue(row in parsed_response[0])
        for row in expected_c_result:
            self.assertTrue(row in parsed_response[1])

    def test_node_list_with_active_node(self):
        self.fake_authentication()
        expected_t_result = [
                [267, 265, 5400, 2200, 0, 5, -1, 235, True],
                [279, 267, 5530, 2465, 0, 5, -1, 235, True],
                [281, 279, 5675, 2635, 0, 5, -1, 235, True],
                [283, 281, 5985, 2745, 0, 5, -1, 235, True],
                [285, 283, 6100, 2980, 0, 5, -1, 235, True],
                [289, 285, 6210, 3480, 0, 5, -1, 235, True],
                [367, None, 7030, 1980, 0, 5, -1, 361, 3],
                [377, None, 7620, 2890, 0, 5, -1, 373, True],
                [403, 377, 7840, 2380, 0, 5, -1, 373, True],
                [405, 377, 7390, 3510, 0, 5, -1, 373, True],
                [407, 405, 7080, 3960, 0, 5, -1, 373, True],
                [409, 407, 6630, 4330, 0, 5, -1, 373, True],
                [415, 289, 5810, 3950, 0, 5, -1, 235, True],
                [417, 415, 4990, 4200, 0, 5, -1, 235, True],
                [2419, 2417, 5040, 5650, 0, 5, -1, 2411, True],
                [2417, 2415, 4400, 5730, 0, 5, -1, 2411, True],
                [2423, 2415, 4140, 6460, 0, 5, -1, 2411, 3]
        ]
        expected_c_result = [
                [356, 6730.0, 2700.0, 0.0, 5, [[285, 5]], [[377, 5], [367, 5]], [], True],
                [421, 6260.0, 3990.0, 0.0, 5, [[415, 5]], [[409, 5]], [], True]
        ]

        response = self.client.post('/%d/node/list' % (self.test_project_id,), {
                'z1': 0,
                'top': 2280,
                'left': 4430,
                'right': 12430,
                'bottom': 5730,
                'z2': 9,
                'atnid': 2423,
                'labels': False,})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(4, len(parsed_response))
        self.assertEqual(len(expected_t_result), len(parsed_response[0]))
        self.assertEqual(len(expected_c_result), len(parsed_response[1]))
        for row in expected_t_result:
            self.assertTrue(row in parsed_response[0])
        for row in expected_c_result:
            self.assertTrue(row in parsed_response[1])

    def test_textlabels_empty(self):
        self.fake_authentication()
        expected_result = {}

        response = self.client.post('/%d/textlabel/all' % (self.test_project_id,), {
                'sid': 3,
                'z': 9,
                'top': 0,
                'left': 0,
                'width': 10240,
                'height': 7680,
                'scale': 0.5,
                'resolution': 5})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response)

    def test_textlabels_nonempty(self):
        self.fake_authentication()
        expected_result = {
                '0': {
                    'tid': 1,
                    'type': 'text',
                    'text': 'World.',
                    'font_name': None,
                    'font_style': 'bold',
                    'font_size': 160,
                    'scaling': 1,
                    'z_diff': 0,
                    'colour': {'r': 255, 'g': 126, 'b': 0, 'a': 1},
                    'location': {'x': 3155, 'y': 1775, 'z': 27}},
                '1': {
                    'tid': 2,
                    'type': 'text',
                    'text': 'Helo.',
                    'font_name': None,
                    'font_style': 'bold',
                    'font_size': 160,
                    'scaling': 1,
                    'z_diff': 0,
                    'colour': {'r': 255, 'g': 126, 'b': 0, 'a': 1},
                    'location': {'x': 2345, 'y': 1785, 'z': 27}}}

        response = self.client.post('/%d/textlabel/all' % (self.test_project_id,), {
                'sid': 3,
                'z': 27,
                'top': 0,
                'left': 0,
                'width': 10240,
                'height': 7680,
                'scale': 0.5,
                'resolution': 5})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        self.assertEqual(expected_result, parsed_response)

    def test_skeleton_list(self):
        self.fake_authentication()

        # Query all skeletons
        url = '/%d/skeletons/' % self.test_project_id
        response = self.client.get(url)
        parsed_response = json.loads(response.content)
        expected_result = frozenset([2388, 235, 373, 2411, 1, 361, 2364, 2451, 2440, 2433])
        self.assertEqual(expected_result, frozenset(parsed_response))
        # Also check response length to be sure there were no duplicates.
        self.assertEqual(len(expected_result), len(parsed_response))

        # Query skeletons of user 2
        response = self.client.get(url, {'created_by': 2})
        parsed_response = json.loads(response.content)
        expected_result = [2364]
        self.assertEqual(expected_result, parsed_response)

        # Query skeletons of user 2 on a date where no neuron was created
        response = self.client.get(url, {'created_by': 2, 'to': '19990505'})
        parsed_response = json.loads(response.content)
        expected_result = []
        self.assertEqual(expected_result, parsed_response)

        # Query skeletons of user 3 on a date where neurons where created
        response = self.client.get(url, {'created_by': 3, 'from': '20111209', 'to': '20111210'})
        parsed_response = json.loads(response.content)
        expected_result = frozenset([2411, 2388])
        self.assertEqual(expected_result, frozenset(parsed_response))
        # Also check response length to be sure there were no duplicates.
        self.assertEqual(len(expected_result), len(parsed_response))

    def test_skeleton_graph(self):
        self.fake_authentication()

        skeleton_ids = [235, 361, 373]
        # Basic graph
        response = self.client.post(
            '/%d/skeletons/confidence-compartment-subgraph' % self.test_project_id,
            {'skeleton_ids[0]': skeleton_ids[0],
             'skeleton_ids[1]': skeleton_ids[1],
             'skeleton_ids[2]': skeleton_ids[2]})
        parsed_response = json.loads(response.content)
        expected_result_edges = [
                [235, 361, [0, 0, 0, 0, 1]],
                [235, 373, [0, 0, 0, 0, 2]]]
        # Since order is not important, check length and matches separately.
        self.assertEqual(len(expected_result_edges), len(parsed_response['edges']))
        for row in expected_result_edges:
            self.assertTrue(row in parsed_response['edges'])

        # Confidence split
        # Change confidence that affects 1 edge from 235 to 373
        response = self.client.post('/%d/treenodes/289/confidence' % self.test_project_id,
            {'new_confidence': 3})
        self.assertEqual(response.status_code, 200)
        # Add confidence criteria, but not one that should affect the graph.
        response = self.client.post(
            '/%d/skeletons/confidence-compartment-subgraph' % self.test_project_id,
            {'skeleton_ids[0]': skeleton_ids[0],
             'skeleton_ids[1]': skeleton_ids[1],
             'skeleton_ids[2]': skeleton_ids[2],
             'confidence_threshold': 2})
        parsed_response = json.loads(response.content)
        expected_result_nodes = frozenset(['235', '361', '373'])
        expected_result_edges = [
                ['235', '361', [0, 0, 0, 0, 1]],
                ['235', '373', [0, 0, 0, 0, 2]]]
        self.assertEqual(expected_result_nodes, frozenset(parsed_response['nodes']))
        # Since order is not important, check length and matches separately.
        self.assertEqual(len(expected_result_edges), len(parsed_response['edges']))
        for row in expected_result_edges:
            self.assertTrue(row in parsed_response['edges'])

        # Use confidence criteria that should split edges from 235 to 373.
        response = self.client.post(
            '/%d/skeletons/confidence-compartment-subgraph' % self.test_project_id,
            {'skeleton_ids[0]': skeleton_ids[0],
             'skeleton_ids[1]': skeleton_ids[1],
             'skeleton_ids[2]': skeleton_ids[2],
             'confidence_threshold': 4})
        parsed_response = json.loads(response.content)
        expected_result_nodes = frozenset(['235_1', '235_2', '361', '373'])
        expected_result_edges = [
                ['235_1', '361', [0, 0, 0, 0, 1]],
                ['235_1', '373', [0, 0, 0, 0, 1]],
                ['235_2', '373', [0, 0, 0, 0, 1]]]
        self.assertEqual(expected_result_nodes, frozenset(parsed_response['nodes']))
        # Since order is not important, check length and matches separately.
        self.assertEqual(len(expected_result_edges), len(parsed_response['edges']))
        for row in expected_result_edges:
            self.assertTrue(row in parsed_response['edges'])

        # Dual split
        # Again split with confidence, but also cluster the split synapses
        # together with bandwidth.
        response = self.client.post(
            '/%d/skeletons/confidence-compartment-subgraph' % self.test_project_id,
            {'skeleton_ids[0]': skeleton_ids[0],
             'skeleton_ids[1]': skeleton_ids[1],
             'skeleton_ids[2]': skeleton_ids[2],
             'expand[0]': skeleton_ids[0],
             'confidence_threshold': 4,
             'bandwidth': 2000})
        parsed_response = json.loads(response.content)
        expected_result_nodes = frozenset(['235_1_1', '235_1_2', '235_2', '361', '373'])
        expected_result_edges = [
                ['235_1_1', '361', [0, 0, 0, 0, 1]],
                ['235_1_1', '373', [0, 0, 0, 0, 1]],
                ['235_2',   '373', [0, 0, 0, 0, 1]]]
        self.assertEqual(expected_result_nodes, frozenset(parsed_response['nodes']))
        # Since order is not important, check length and matches separately.
        self.assertEqual(len(expected_result_edges), len(parsed_response['edges']))
        for row in expected_result_edges:
            self.assertTrue(row in parsed_response['edges'])

    def test_annotation_creation(self):
        self.fake_authentication()

        neuron_ids = [2365, 2381]
        # Expect entity 2365 and 2381 to be not annotated
        for nid in neuron_ids:
            aq = create_annotation_query(self.test_project_id, {'neuron_id': nid})
            self.assertEqual(len(aq), 0)

        # Annotate both with the same annotation
        _annotate_entities(self.test_project_id, neuron_ids,
                {'myannotation': self.test_user_id})

        # Expect entity 2365 and 2381 to be annotated
        for nid in neuron_ids:
            aq = create_annotation_query(self.test_project_id, {'neuron_id': nid})
            self.assertEqual(len(aq), 1)
            self.assertEqual(aq[0].name, 'myannotation')

        # Annotate both with the pattern annotation
        _annotate_entities(self.test_project_id, neuron_ids,
                {'pattern {n9} test-{n}-annotation': self.test_user_id})

        # Expect entity 2365 and 2381 to be annotated
        aq = create_annotation_query(self.test_project_id, {'neuron_id': 2365}).order_by('name')
        self.assertEqual(len(aq), 2)
        self.assertEqual(aq[0].name, 'myannotation')
        self.assertEqual(aq[1].name, 'pattern 9 test-1-annotation')
        aq = create_annotation_query(self.test_project_id, {'neuron_id': 2381}).order_by('name')
        self.assertEqual(len(aq), 2)
        self.assertEqual(aq[0].name, 'myannotation')
        self.assertEqual(aq[1].name, 'pattern 10 test-2-annotation')

    def test_review_status(self):
        self.fake_authentication()

        skeleton_id = 2388

        # No reviews, single segment
        url = '/%d/skeletons/review-status' % (self.test_project_id)
        response = self.client.post(url, {'skeleton_ids[0]': skeleton_id})
        self.assertEqual(response.status_code, 200)
        expected_result = {'2388': [3, 0]}
        self.assertJSONEqual(response.content, expected_result)

        # Add reviews
        review_time = "2014-03-17T00:00:00Z"
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2396)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=2,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2396)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2394)
        response = self.client.post(url, {'skeleton_ids[0]': skeleton_id})
        self.assertEqual(response.status_code, 200)
        expected_result = {'2388': [3, 2]}
        self.assertJSONEqual(response.content, expected_result)

        # Use empty whitelist
        response = self.client.post(url,
                {'skeleton_ids[0]': skeleton_id, 'whitelist': 'true'})
        self.assertEqual(response.status_code, 200)
        expected_result = {'2388': [3, 0]}
        self.assertJSONEqual(response.content, expected_result)

        # Add a user to whitelist
        ReviewerWhitelist.objects.create(project_id=self.test_project_id,
                user_id=self.test_user_id, reviewer_id=2, accept_after=review_time)
        response = self.client.post(url,
                {'skeleton_ids[0]': skeleton_id, 'whitelist': 'true'})
        self.assertEqual(response.status_code, 200)
        expected_result = {'2388': [3, 1]}
        self.assertJSONEqual(response.content, expected_result)

    def test_export_review_skeleton(self):
        self.fake_authentication()

        skeleton_id = 2388

        # No reviews, single segment
        url = '/%d/skeletons/%d/review' % (self.test_project_id, skeleton_id)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        expected_result = [{'status': '0.00', 'id': 0, 'nr_nodes': 3, 'sequence': [
                {'y': 6550.0, 'x': 3680.0, 'z': 0.0, 'rids': [], 'sup': [], 'id': 2396},
                {'y': 6030.0, 'x': 3110.0, 'z': 0.0, 'rids': [], 'sup': [], 'id': 2394},
                {'y': 6080.0, 'x': 2370.0, 'z': 0.0, 'rids': [], 'sup': [], 'id': 2392}]}]
        self.assertJSONEqual(response.content, expected_result)

        # Add reviews
        review_time = "2014-03-17T00:00:00Z"
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2396)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=2,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2396)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2394)
        response = self.client.post(url)
        self.assertEqual(response.status_code, 200)
        expected_result = [{'status': '66.67', 'id': 0, 'nr_nodes': 3, 'sequence': [
                {'y': 6550.0, 'x': 3680.0, 'z': 0.0, 'rids': [[3, review_time], [2, review_time]], 'sup': [], 'id': 2396},
                {'y': 6030.0, 'x': 3110.0, 'z': 0.0, 'rids': [[3, review_time]], 'sup': [], 'id': 2394},
                {'y': 6080.0, 'x': 2370.0, 'z': 0.0, 'rids': [], 'sup': [], 'id': 2392}]}]
        self.assertJSONEqual(response.content, expected_result)

        # Newer reviews of same nodes should duplicate reviewer ID
        # NOTE: this duplication does not happen in practice because
        # update_location_reviewer updates the timestamp of the existing
        # review. This is just to demonstrate what edge case behavior is.
        review_time = "2014-03-18T00:00:00Z"
        Review.objects.create(project_id=self.test_project_id, reviewer_id=2,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2396)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=2394)
        response = self.client.post(url)
        expected_result[0]['sequence'][0]['rids'].append([2, review_time])
        expected_result[0]['sequence'][1]['rids'].append([3, review_time])
        self.assertEqual(response.status_code, 200)
        self.assertJSONEqual(response.content, expected_result)

        # Test subarbor support
        response = self.client.post(url, {'subarbor_node_id': 2394})
        self.assertEqual(response.status_code, 200)
        expected_result[0]['status'] = '100.00'
        expected_result[0]['nr_nodes'] = 2
        del expected_result[0]['sequence'][-1]
        self.assertJSONEqual(response.content, expected_result)

    def test_export_skeleton_reviews(self):
        self.fake_authentication()

        skeleton_id = 235

        # No reviews
        url = '/%d/skeleton/%d/reviewed-nodes' % (self.test_project_id, skeleton_id)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = {}
        self.assertEqual(expected_result, parsed_response)

        review_time = "2014-03-17T18:14:34.851Z"
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=253)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=2,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=253)
        Review.objects.create(project_id=self.test_project_id, reviewer_id=3,
            review_time=review_time, skeleton_id=skeleton_id, treenode_id=263)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        expected_result = {
                '253': [[3, review_time], [2, review_time]],
                '263': [[3, review_time]]}
        self.assertJSONEqual(response.content, expected_result)

    def test_user_reviewer_whitelist(self):
        self.fake_authentication()

        # Test that whitelist is empty by default.
        url = '/%d/user/reviewer-whitelist' % (self.test_project_id,)
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []
        self.assertEqual(expected_result, parsed_response)

        # Test replacing whitelist.
        whitelist = {
                '1': "2014-03-17T00:00:00Z",
                '2': "2014-03-18T00:00:00Z"}
        response = self.client.post(url, whitelist)
        self.assertEqual(response.status_code, 200)

        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        expected_result = [{'reviewer_id': int(r), 'accept_after': t}
                for r,t in whitelist.iteritems()]
        self.assertJSONEqual(response.content, expected_result)

    def test_suppressed_virtual_nodes(self):
        self.fake_authentication()

        response = self.client.post(
                '/%d/treenode/create' % (self.test_project_id, ),
                {'x': 1,
                 'y': -1,
                 'z': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        parent_id = parsed_response['treenode_id']
        skeleton_id = parsed_response['skeleton_id']

        response = self.client.post(
                '/%d/treenode/create' % (self.test_project_id, ),
                {'x': 3,
                 'y': -3,
                 'z': 2,
                 'parent_id': parent_id})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        child_id = parsed_response['treenode_id']

        # Initially no nodes should be supppressed
        response = self.client.get(
                '/%d/treenodes/%d/suppressed-virtual/' % (self.test_project_id, child_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_result = []
        self.assertEqual(expected_result, parsed_response)

        # Reject attempt to suppress root node
        response = self.client.post(
                '/%d/treenodes/%d/suppressed-virtual/' % (self.test_project_id, parent_id),
                {'location_coordinate': 1,
                 'orientation': 0})
        self.assertEqual(response.status_code, 400)

        # Reject coordinate outside edge
        response = self.client.post(
                '/%d/treenodes/%d/suppressed-virtual/' % (self.test_project_id, child_id),
                {'location_coordinate': 4,
                 'orientation': 0})
        self.assertEqual(response.status_code, 400)

        # Create virtual node
        response = self.client.post(
                '/%d/treenodes/%d/suppressed-virtual/' % (self.test_project_id, child_id),
                {'location_coordinate': 2,
                 'orientation': 0})
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        suppressed_id = parsed_response['id']

        # Delete virtual node
        response = self.client.delete(
                '/%d/treenodes/%d/suppressed-virtual/%d' % (self.test_project_id, child_id, suppressed_id))
        self.assertEqual(response.status_code, 204)

    def test_export_compact_skeleton(self):
        self.fake_authentication()

        skeleton_id = 373
        response = self.client.post(
                '/%d/%d/1/1/compact-skeleton' % (self.test_project_id, skeleton_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_response = [
                [[377, None, 3, 7620.0, 2890.0, 0.0, -1.0, 5],
                 [403, 377, 3, 7840.0, 2380.0, 0.0, -1.0, 5],
                 [405, 377, 3, 7390.0, 3510.0, 0.0, -1.0, 5],
                 [407, 405, 3, 7080.0, 3960.0, 0.0, -1.0, 5],
                 [409, 407, 3, 6630.0, 4330.0, 0.0, -1.0, 5]],
                [[377, 356, 1, 6730.0, 2700.0, 0.0],
                 [409, 421, 1, 6260.0, 3990.0, 0.0]],
                {"uncertain end": [403]}]
        self.assertItemsEqual(parsed_response[0], expected_response[0])
        self.assertItemsEqual(parsed_response[1], expected_response[1])
        self.assertEqual(parsed_response[2], expected_response[2])

    def test_export_compact_arbor(self):
        self.fake_authentication()

        skeleton_id = 373
        response = self.client.post(
                '/%d/%d/1/1/1/compact-arbor' % (self.test_project_id, skeleton_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_response = [
                [[377, None, 3, 7620.0, 2890.0, 0.0, -1.0, 5],
                 [403, 377, 3, 7840.0, 2380.0, 0.0, -1.0, 5],
                 [405, 377, 3, 7390.0, 3510.0, 0.0, -1.0, 5],
                 [407, 405, 3, 7080.0, 3960.0, 0.0, -1.0, 5],
                 [409, 407, 3, 6630.0, 4330.0, 0.0, -1.0, 5]],
                [[377, 5, 356, 5, 285, 235, 1, 0],
                 [409, 5, 421, 5, 415, 235, 1, 0]],
                {"uncertain end": [403]}]
        self.assertItemsEqual(parsed_response[0], expected_response[0])
        self.assertItemsEqual(parsed_response[1], expected_response[1])
        self.assertEqual(parsed_response[2], expected_response[2])

    def test_export_compact_arbor_with_minutes(self):
        self.fake_authentication()

        skeleton_id = 373
        response = self.client.post(
                '/%d/%d/1/1/1/compact-arbor-with-minutes' % (self.test_project_id, skeleton_id))
        self.assertEqual(response.status_code, 200)
        parsed_response = json.loads(response.content)
        expected_response = [
                [[377, None, 3, 7620.0, 2890.0, 0.0, -1.0, 5],
                 [403, 377, 3, 7840.0, 2380.0, 0.0, -1.0, 5],
                 [405, 377, 3, 7390.0, 3510.0, 0.0, -1.0, 5],
                 [407, 405, 3, 7080.0, 3960.0, 0.0, -1.0, 5],
                 [409, 407, 3, 6630.0, 4330.0, 0.0, -1.0, 5]],
                [[377, 5, 356, 5, 285, 235, 1, 0],
                 [409, 5, 421, 5, 415, 235, 1, 0]],
                {"uncertain end": [403]},
                {"21951837": [377, 403, 405, 407, 409]}]
        self.assertItemsEqual(parsed_response[0], expected_response[0])
        self.assertItemsEqual(parsed_response[1], expected_response[1])
        self.assertEqual(parsed_response[2], expected_response[2])
        for k, v in expected_response[3].iteritems():
            self.assertItemsEqual(parsed_response[3][k], v)


class TreenodeTests(TestCase):
    fixtures = ['catmaid_testdata']

    maxDiff = None

    def setUp(self):
        self.test_project_id = 3

    def test_find_all_treenodes(self):

        # These next two could be done in one query, of course:
        neuron = ClassInstance.objects.get(name='branched neuron',
                                           class_column__class_name='neuron')
        skeleton = ClassInstance.objects.get(
            class_column__class_name='skeleton',
            cici_via_a__relation__relation_name='model_of',
            cici_via_a__class_instance_b=neuron)

        tns = Treenode.objects.filter(
            treenodeclassinstance__class_instance=skeleton).order_by('id')

        self.assertEqual(len(tns), 28)

        self.assertEqual(tns[0].id, 237)

        # That's a root node, so parent should be None:
        self.assertTrue(tns[0].parent is None)

        # But the next should have this as a parent:
        self.assertEqual(tns[1].parent, tns[0])

        x = tns[0].location_x
        y = tns[0].location_y
        z = tns[0].location_z

        self.assertTrue(1030 < x < 1090)
        self.assertTrue(3000 < y < 3060)
        self.assertTrue(-30 < z < 30)

        # There should be 2 connectors attached to the skeleton via
        # treenodes:

        connectors = Connector.objects.filter(
            treenodeconnector__treenode__treenodeclassinstance__class_instance=skeleton)
        self.assertEqual(len(connectors), 3)

class PermissionTests(TestCase):
    fixtures = ['catmaid_testdata']

    def setUp(self):
        self.test_project_id = 3
        self.client = Client()

        # Set up test API. Because we want to test only general access to the
        # methods, it doesn't matter if we use fake parameters.
        url_params = {
            'pid': self.test_project_id,
            'skid': 123456,
            'sid': 1,
        }
        self.can_browse_get_api = [
            '/permissions',
            '/accounts/login',
            '/user-list',
            '/%(pid)s/stacks' % url_params,
            '/%(pid)s/search' % url_params,
            '/%(pid)s/tracing/setup/test' % url_params,
            '/%(pid)s/stats' % url_params,
            '/%(pid)s/stats/nodecount' % url_params,
            '/%(pid)s/stats/user-history' % url_params,
        ]
        self.can_browse_post_api = [
            '/permissions',
            '/accounts/login',
            '/user-table-list',
            '/user-profile/update',
            '/%(pid)s/notifications/list' % url_params,
            '/%(pid)s/node/user-info' % url_params,
            '/%(pid)s/node/get_location' % url_params,
            '/%(pid)s/node/list' % url_params,
            '/%(pid)s/skeletons/confidence-compartment-subgraph' % url_params,
            '/%(pid)s/graph/circlesofhell' % url_params,
            '/%(pid)s/connector/list/one_to_many' % url_params,
            '/%(pid)s/%(skid)s/1/1/0/compact-arbor' % url_params,
            '/%(pid)s/annotations/forskeletons' % url_params,
            '/%(pid)s/annotations/table-list' % url_params,
            '/%(pid)s/skeleton/analytics' % url_params,
            '/%(pid)s/skeleton/annotationlist' % url_params,
            '/%(pid)s/skeletons/review-status' % url_params,
            '/%(pid)s/skeleton/%(skid)s/neuronname' % url_params,
            '/%(pid)s/skeleton/connectors-by-partner' % url_params,
            '/%(pid)s/stack/%(sid)s/models' % url_params,
            '/%(pid)s/logs/list' % url_params,
            '/%(pid)s/graphexport/json' % url_params,
            '/%(pid)s/neuroml/neuroml_level3_v181' % url_params,
            '/%(pid)s/treenodearchive/export' % url_params,
            '/%(pid)s/connectorarchive/export' % url_params,
        ]

    def test_user_permissions(self):
            response = self.client.get("/permissions")
            self.assertEqual(response.status_code, 200)
            # Expect [{}, []] as result, because the anonymous user is
            # currently not assigned any permissions
            self.assertJSONEqual(response.content, [{},[]])

    def test_can_browse_access(self):
        # Give anonymous user browse permissions for the test project
        anon_user = User.objects.get(pk=settings.ANONYMOUS_USER_ID)
        p = Project.objects.get(pk=self.test_project_id)
        assign_perm('can_browse', anon_user, p)
        # Give anonymous user general browse permissions
        permission = Permission.objects.get(codename='can_browse')
        anon_user.user_permissions.add(permission)


        # Make sure we get no permission error on anonymous accessible get
        # methods
        for api in self.can_browse_get_api:
            msg = "GET %s" % api
            response = self.client.get(api)
            self.assertEqual(response.status_code, 200, msg)
            try:
                parsed_response = json.loads(response.content)
                self.assertFalse('permission_error' in parsed_response, msg)
            except ValueError, e:
                # If a response is no JSON, everything is fine as well
                if str(e) != "No JSON object could be decoded":
                    raise e

        # Make sure we get no permission error on anonymous accessible post
        # methods
        for api in self.can_browse_post_api:
            msg = "POST %s" % api
            response = self.client.post(api)
            self.assertEqual(response.status_code, 200, msg)
            try:
                parsed_response = json.loads(response.content)
                self.assertFalse('permission_error' in parsed_response, msg)
            except ValueError, e:
                # If a response is no JSON, everything is fine as well
                if str(e) != "No JSON object could be decoded":
                    raise e
