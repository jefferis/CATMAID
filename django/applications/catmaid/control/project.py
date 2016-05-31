import json

from guardian.shortcuts import get_objects_for_user

from django.db import connection
from django.http import HttpResponse
from django.shortcuts import get_object_or_404

from catmaid.models import UserRole, Class, Project, Relation, StackGroup
from catmaid.control.authentication import requires_user_role

from rest_framework.decorators import api_view


@requires_user_role([UserRole.Annotate, UserRole.Browse])
def list_project_tags(request, project_id=None):
    """ Return the tags associated with the project.
    """
    p = get_object_or_404(Project, pk=project_id)
    tags = [ str(t) for t in p.tags.all()]
    result = {'tags':tags}
    return HttpResponse(json.dumps(result, sort_keys=True, indent=4), content_type="application/json")

@requires_user_role([UserRole.Annotate, UserRole.Browse])
def update_project_tags(request, project_id=None, tags=None):
    """ Updates the given project with the supplied tags. All
    existing tags will be replaced.
    """
    p = get_object_or_404(Project, pk=project_id)
    # Create list of sigle stripped tags
    if tags is None:
        tags = []
    else:
        tags = tags.split(",")
        tags = [t.strip() for t in tags]

    # Add tags to the model
    p.tags.set(*tags)

    # Return an empty closing response
    return HttpResponse(json.dumps(""), content_type="application/json")

class ExProject:
    """ A wrapper around the Project model to include additional
    properties.
    """
    def __init__(self, project, is_catalogueable):
        self.project = project
        self.is_catalogueable = is_catalogueable

    def __getattr__(self, attr):
        """ Return own property when available, otherwise proxy
        to project.
        """
        if attr in self.__dict__:
            return getattr(self,attr)
        return getattr(self.project, attr)

def extend_projects(user, projects):
    """ Adds the is_catalogueable property to all projects passed.
    """
    # Find all the projects that are catalogueable:
    catalogueable_projects = set(x.project.id for x in \
        Class.objects.filter(class_name='driver_line').select_related('project'))

    result = []
    for p in projects:
        ex_p = ExProject(p, id in catalogueable_projects)
        result.append(ex_p)

    return result

def get_project_qs_for_user(user):
    """ Returns the query set of projects that are administrable and
    browsable by the given user.
    """
    perms=['can_administer', 'can_annotate', 'can_browse']
    return get_objects_for_user(user, perms, Project, any_perm=True,
                                 accept_global_perms=False)

@api_view(['GET'])
def projects(request):
    """ List projects visible to the requesting user.
    ---
    models:
      project_api_stack_element:
        id: project_api_stack_element
        properties:
          id:
            type: integer
            description: Stack ID
            required: true
          title:
            type: string
            description: Stack title
            required: true
          comment:
            type: string
            description: Comment on stack
            required: true
      project_api_stackgroup_element:
        id: project_api_stackgroup_element
        properties:
          id:
            type: integer
            description: Stack group ID
            required: true
          title:
            type: string
            description: Stack group title
            required: true
          comment:
            type: string
            description: Comment on stack group
            required: true
      project_api_element:
        id: project_api_element
        properties:
          id:
            type: integer
            description: Project ID
            required: true
          title:
            type: string
            description: Project title
            required: true
          catalogue:
            type: boolean
            description: Indicates if the project has a catalogue entry
            required: true
          stacks:
            type: array
            items:
              $ref: project_api_stack_element
            required: true
          stackgroups:
            type: array
            items:
              $ref: project_api_stackgroup_element
            required: true
    type:
      projects:
        type: array
        items:
          $ref: project_api_element
        required: true
    """

    # Get all projects that are visisble for the current user
    projects = get_project_qs_for_user(request.user).order_by('title')
    cursor = connection.cursor()
    project_template = ",".join(("(%s)",) * len(projects))
    user_project_ids = [p.id for p in projects]
    cursor.execute("""
        SELECT ps.project_id, ps.stack_id, s.title, s.comment FROM project_stack ps
        INNER JOIN (VALUES {}) user_project(id)
        ON ps.project_id = user_project.id
        INNER JOIN stack s
        ON ps.stack_id = s.id
    """.format(project_template), user_project_ids)
    project_stack_mapping = dict()
    for row in cursor.fetchall():
        stacks = project_stack_mapping.get(row[0])
        if not stacks:
            stacks = []
            project_stack_mapping[row[0]] = stacks
        stacks.append({
            'id': row[1],
            'title': row[2],
            'comment': row[3]
        })

    # Extend projects with extra catalogueable info
    projects = extend_projects(request.user, projects)

    # Get all stack groups for this project
    project_stack_groups = {}
    for group in StackGroup.objects.all():
        groups = project_stack_groups.get(group.project_id)
        if not groups:
            groups = []
            project_stack_groups[group.project_id] = groups
        groups.append(group)

    result = []
    no_stacks = tuple()
    for p in projects:
        stacks = project_stack_mapping.get(p.id, no_stacks)

        stackgroups = []
        available_stackgroups = project_stack_groups.get(p.id)
        if available_stackgroups:
            for sg in available_stackgroups:
                stackgroups.append({
                    'id': sg.id,
                    'title': sg.name,
                    'comment': '',
                })

        result.append({
            'id': p.id,
            'title': p.title,
            'catalogue': int(p.is_catalogueable),
            'stacks': stacks,
            'stackgroups': stackgroups
        })

    return HttpResponse(json.dumps(result, sort_keys=True, indent=4), content_type="application/json")
