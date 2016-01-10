import re
from django import forms
from django.core.exceptions import ValidationError
from django.db import models
from widgets import Double3DWidget, Integer3DWidget, RGBAWidget

# ------------------------------------------------------------------------
# Classes to support the integer3d compound type:

class Integer3D(object):

    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z

    integer_re = '[-+0-9]+'
    tuple_pattern = re.compile('^\((%s),\s*(%s),\s*(%s)\)$'%((integer_re,)*3))

    @classmethod
    def from_str(cls, s):
        m = cls.tuple_pattern.match(s)
        if m:
            return Integer3D(x=int(m.group(1), 10),
                             y=int(m.group(2), 10),
                             z=int(m.group(3), 10))
        else:
            raise ValidationError("Couldn't parse value as an Integer3D: " + str(s))

    def __unicode__(self):
        return u"(%d, %d, %d)" % (self.x, self.y, self.z)

class Integer3DField(models.Field):

    def formfield(self, **kwargs):
        defaults = {'form_class': Integer3DFormField}
        defaults.update(kwargs)
        return super(Integer3DField, self).formfield(**defaults)

    def db_type(self, connection):
        return 'integer3d'

    def from_db_value(self, value, expression, connection, context):
        if value is None:
            return value

        return Integer3D.from_str(value)

    def to_python(self, value):
        if isinstance(value, Integer3D):
            return value
        elif (isinstance(value, list) or isinstance(value, tuple)) and len(value) == 3:
            return Integer3D(value[0], value[1], value[2])
        # When contructing a Location, we get the empty string
        # here; return a new Integer3D for any falsy value:
        elif not value:
            return Integer3D()
        else:
            return Integer3D.from_str(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        value = self.to_python(value)
        return "(%d,%d,%d)" % (value.x, value.y, value.z)


# ------------------------------------------------------------------------
# Classes to support the double3d compound type:

class Double3D(object):

    def __init__(self, x=0, y=0, z=0):
        self.x, self.y, self.z = x, y, z

    double_re = '[-+0-9\.Ee]+'
    tuple_pattern = re.compile(r'^\((%s),\s*(%s),\s*(%s)\)$' % ((double_re,)*3))

    @classmethod
    def from_str(cls, s):
        m = cls.tuple_pattern.match(s)
        if m:
            return Double3D(x=float(m.group(1)),
                            y=float(m.group(2)),
                            z=float(m.group(3)))
        else:
            raise ValidationError("Couldn't parse value from the database as a Double3D: " + str(s))

    def __unicode__(self):
        return u"(%.3f, %.3f, %.3f)" % (self.x, self.y, self.z)

class Double3DField(models.Field):

    def formfield(self, **kwargs):
        defaults = {'form_class': Double3DFormField}
        defaults.update(kwargs)
        return super(Double3DField, self).formfield(**defaults)

    def db_type(self, connection):
        return 'double3d'

    def from_db_value(self, value, expression, connection, context):
        if value is None:
            return value

        return Double3D.from_str(value)

    def to_python(self, value):
        if isinstance(value, Double3D):
            return value
        elif (isinstance(value, list) or isinstance(value, tuple)) and len(value) == 3:
            return Double3D(value[0], value[1], value[2])
        # When contructing a Location, we get the empty string
        # here; return a new Double3D for any falsy value:
        elif not value or value == '(,,)':
            return Double3D()
        else:
            return Double3D.from_str(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        value = self.to_python(value)
        return "(%f,%f,%f)" % (value.x, value.y, value.z)

# ------------------------------------------------------------------------
# Classes to support the rgba compound type:

class RGBA(object):

    def __init__(self, r=0, g=0, b=0, a=0):
        self.r, self.g, self.b, self.a = r, g, b, a

    double_re = '[-+0-9\.Ee]+'
    tuple_pattern = re.compile(r'^\((%s),\s*(%s),\s*(%s),\s*(%s)\)$' % ((double_re,)*4))

    @classmethod
    def from_str(cls, s):
        m = cls.tuple_pattern.match(s)
        if m:
            return RGBA(r=float(m.group(1)),
                        g=float(m.group(2)),
                        b=float(m.group(3)),
                        a=float(m.group(4)))
        else:
            raise ValidationError("Couldn't parse value as an RGBA: " + str(s))

    def hex_color(self):
        return "#{0:06x}".format((int(self.r * 255) << 16) + (int(self.g * 255) << 8) + int(self.b * 255))

    def __unicode__(self):
        return u"(%.3f, %.3f, %.3f, %.3f)" % (self.r, self.g, self.b, self.a)

class RGBAField(models.Field):

    def formfield(self, **kwargs):
        defaults = {'form_class': RGBAFormField}
        defaults.update(kwargs)
        return super(RGBAField, self).formfield(**defaults)

    def db_type(self, connection):
        return 'rgba'

    def from_db_value(self, value, expression, connection, context):
        if value is None:
            return value

        return RGBA.from_str(value)

    def to_python(self, value):
        if isinstance(value, RGBA):
            return value
        elif (isinstance(value, list) or isinstance(value, tuple)) and len(value) == 3:
            return RGBA(value[0], value[1], value[2], 1)
        elif (isinstance(value, list) or isinstance(value, tuple)) and len(value) == 4:
            return RGBA(value[0], value[1], value[2], value[3])
        # When contructing a Location, we get the empty string
        # here; return a new RGBA for any falsy value:
        elif not value:
            return RGBA()
        elif isinstance(value, str) or isinstance(value, unicode):
            return RGBA.from_str(value)
        else:
            return RGBA()    #.from_str(value)

    def get_db_prep_value(self, value, connection, prepared=False):
        value = self.to_python(value)
        return "(%f,%f,%f,%f)" % (value.r, value.g, value.b, value.a)

# ------------------------------------------------------------------------

class Integer3DFormField(forms.MultiValueField):
    widget = Integer3DWidget

    def __init__(self, *args, **kwargs):
        fields = (
            forms.IntegerField(label='X'),
            forms.IntegerField(label='Y'),
            forms.IntegerField(label='Z'),
        )
        super(Integer3DFormField, self).__init__(fields, *args, **kwargs)

    def compress(self, data_list):
        if data_list:
            return data_list
        return [None, None, None]

class Double3DFormField(forms.MultiValueField):
    widget = Double3DWidget

    def __init__(self, *args, **kwargs):
        fields = (
            forms.FloatField(label='X'),
            forms.FloatField(label='Y'),
            forms.FloatField(label='Z'),
        )
        super(Double3DFormField, self).__init__(fields, *args, **kwargs)

    def compress(self, data_list):
        if data_list:
            return data_list
        return [None, None, None]

class RGBAFormField(forms.MultiValueField):
    widget = RGBAWidget

    def __init__(self, *args, **kwargs):
        fields = (
            forms.FloatField(label='R'),
            forms.FloatField(label='G'),
            forms.FloatField(label='B'),
            forms.FloatField(label='A'),
        )
        super(RGBAFormField, self).__init__(fields, *args, **kwargs)

    def compress(self, data_list):
        if data_list:
            return data_list
        return [None, None, None]
