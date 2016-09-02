# standard library
import hashlib
import plistlib
from datetime import datetime
from urllib import quote

# third-party
import unicodecsv as csv

# Django
from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.core.urlresolvers import reverse
from django.http import (HttpResponse, HttpResponseNotFound,
                         HttpResponseBadRequest)
from django.shortcuts import get_object_or_404, render_to_response
from django.template import RequestContext
from django.views.decorators.csrf import csrf_exempt
from django.views.generic import DetailView, View
# from datatableview import Datatable, ValuesDatatable
# from datatableview.columns import TextColumn
# from datatableview.views import DatatableView
from datatableview.views.legacy import LegacyDatatableView

# local Django
from models import Application, Inventory, InventoryItem
from server import utils
from sal.decorators import class_login_required, class_access_required
from server.models import BusinessUnit, MachineGroup, Machine


class GroupMixin(object):
    """Mixin to add get_business_unit method for access decorators."""
    classes = {"all": None,
               "business_unit": BusinessUnit,
               "machine": Machine,
               "machine_group": MachineGroup}

    @classmethod
    def get_business_unit(cls, **kwargs):
        """Return the business unit associated with this request."""
        instance = None
        group_class = cls.classes[kwargs["group_type"]]

        if group_class:
            instance = get_object_or_404(
                group_class, pk=kwargs["group_id"])

        # Implicitly returns BusinessUnit, or None if that is the type.
        # No need for an extra test.
        if group_class is MachineGroup:
            instance = instance.business_unit
        elif group_class is Machine:
            instance = instance.machine_group.business_unit

        return instance

    def get_group_instance(self):
        group_type = self.kwargs["group_type"]
        group_class = self.classes[group_type]
        if group_class:
            instance = get_object_or_404(
                group_class, pk=self.kwargs["group_id"])
        else:
            instance = None

        return instance

    def filter_inventoryitem_by_group(self, queryset):
        """Filter the model to only include allowed data.

        Depending on the type of query being performed, filter to only
        include entries of that type (Machine, MachineGroup,
        BusinessUnit).
        """
        self.group_instance = self.get_group_instance()
        # No need to filter if group_instance is None.
        if self.group_instance:
            if isinstance(self.group_instance, BusinessUnit):
                filter_path = "machine__machine_group__business_unit"
            elif isinstance(self.group_instance, MachineGroup):
                filter_path = "machine__machine_group"
            elif isinstance(self.group_instance, Machine):
                filter_path = "machine"

            kwargs = {filter_path: self.group_instance}
            queryset = queryset.filter(**kwargs)

        return queryset


class CSVResponseMixin(object):
    csv_filename = "sal_inventory"
    csv_ext = ".csv"
    components = []
    header = []

    def get_csv_filename(self):
        identifier = "_" + "_".join(self.components) if self.components else ""
        filename = "%s%s%s" % (self.csv_filename, identifier, self.csv_ext)
        return filename

    def set_header(self, headers):
        self.header = headers

    def render_to_csv(self, data):
        response = HttpResponse(content_type='text/csv')
        cd = 'attachment; filename="{0}"'.format(self.get_csv_filename())
        response['Content-Disposition'] = cd

        writer = csv.writer(response)
        if hasattr(self, "header") and self.header:
            writer.writerow(self.header)
        for row in data:
            writer.writerow(row)

        return response


@class_login_required
@class_access_required
class InventoryListView(LegacyDatatableView, GroupMixin):
    model = InventoryItem
    template_name = "inventory/inventory_list.html"
    csv_filename = "sal_inventory_list.csv"
    datatable_options = {
        'structure_template': 'bootstrap_structure.html',
        # 'columns': [('Machine', 'machine', "get_machine_link"),
        #             ("Serial Number", "machine__serial"),
        #             ("Last Checkin", 'machine__last_checkin', 'format_date'),
        #             ("User", "machine__console_user")]}
        'columns': [('Machine', 'machine', "get_machine_link"),
                    ("Serial Number", "serial"),
                    ("Last Checkin", 'last_checkin', 'format_date'),
                    ("User", "console_user"),
                    ("Installed Copies", None, "get_install_count")]}

    def get_queryset(self):
        queryset = self.filter_inventoryitem_by_group(self.model.objects)

        # Filter based on Application.
        self.application = get_object_or_404(
            Application, pk=self.kwargs["application_id"])
        queryset = queryset.filter(application=self.application)

        # Filter again based on criteria.
        field_type = self.kwargs["field_type"]
        if field_type == "path":
            queryset = queryset.filter(path=self.kwargs["field_value"])
        elif field_type == "version":
            queryset = queryset.filter(version=self.kwargs["field_value"])

        # Get a queryset of all of the unique Machines with this
        # Application.
        # This is basically changing the model for this class, which is
        # suspect.
        if is_postgres():
            queryset = queryset.order_by().distinct("machine")
        else:
            machines = queryset.order_by().values_list("machine", flat=True).distinct()
            queryset = Machine.objects.filter(id__in=machines)

        return queryset

    def get_context_data(self, **kwargs):
        context = super(InventoryListView, self).get_context_data(**kwargs)
        context["application_id"] = self.application.id
        context["group_type"] = self.kwargs["group_type"]
        context["group_id"] = self.kwargs["group_id"]
        context["group_name"] = (self.group_instance.name if hasattr(
            self.group_instance, "name") else None)
        context["app_name"] = self.application.name
        context["field_type"] = self.kwargs["field_type"]
        context["field_value"] = self.kwargs["field_value"]
        return context

    def format_date(self, instance, *args, **kwargs):
        return instance.last_checkin.strftime("%Y-%m-%d %H:%M:%S")

    def get_machine_link(self, instance, *args, **kwargs):
        url = reverse(
            "machine_detail", kwargs={"machine_id": instance.pk})

        return '<a href="{}">{}</a>'.format(url, instance.hostname)

    def get_install_count(self, instance, *args, **kwargs):
        queryset = instance.inventoryitem_set.filter(
            application=self.application)
        field_type = self.kwargs["field_type"]
        if field_type == "path":
            queryset = queryset.filter(path=self.kwargs["field_value"])
        elif field_type == "version":
            queryset = queryset.filter(version=self.kwargs["field_value"])
        return queryset.count()


# class ApplicationList(Datatable):

#     class Meta:
#         columns = ['name', 'bundleid', 'bundlename']
#         labels = {'bundleid': 'Bundle ID', 'bundlename': 'Bundle Name'}
#         result_counter_id = ['name']
#         # processors = {'name': link_to_model}
#         structure_template = 'datatableview/bootstrap_structure.html'


@class_login_required
@class_access_required
class ApplicationListView(LegacyDatatableView, GroupMixin):
    model = Application
    template_name = "inventory/application_list.html"
    # datatable_class = ApplicationList
    datatable_options = {
        'structure_template': 'bootstrap_structure.html',
        'columns': [('Name', 'name', "get_name_link"),
                    ("Bundle ID", 'bundleid'),
                    ("Bundle Name", 'bundlename'),
                    ("Install Count", None, "get_install_count")]}

    def get_name_link(self, instance, *args, **kwargs):
        self.kwargs["pk"] = instance.pk
        url = reverse("application_detail", kwargs=self.kwargs)
        return '<a href="{}">{}</a>'.format(url, instance.name)

    def get_install_count(self, instance, *args, **kwargs):
        queryset = self.filter_inventoryitem_by_group(
            instance.inventoryitem_set)

        # Build a link to InventoryListView for install count badge.
        url_kwargs = {
            "group_type": self.kwargs["group_type"],
            "group_id": self.kwargs["group_id"],
            "application_id": instance.pk,
            "field_type": "all",
            "field_value": 0}
        url = reverse("inventory_list", kwargs=url_kwargs)
        anchor = '<a href="%s"><span class="badge">%s</span></a>' % (
            url, queryset.count())
        return anchor

    def get_context_data(self, **kwargs):
        context = super(ApplicationListView, self).get_context_data(**kwargs)
        self.group_instance = self.get_group_instance()
        context["group_type"] = self.kwargs["group_type"]
        if hasattr(self.group_instance, "name"):
            context["group_name"] = self.group_instance.name
        elif hasattr(self.group_instance, "hostname"):
            context["group_name"] = self.group_instance.hostname
        else:
            context["group_name"] = None
        context["group_id"] = (self.group_instance.id if hasattr(
            self.group_instance, "id") else 0)
        context["application_id"] = 0
        context["field_type"] = "all"
        context["field_value"] = 0
        return context


@class_login_required
@class_access_required
class ApplicationDetailView(DetailView, GroupMixin):
    model = Application
    template_name = "inventory/application_detail.html"

    def get_context_data(self, **kwargs):
        details = self._get_filtered_queryset()
        versions, paths = self._get_unique_items(details)
        context = super(ApplicationDetailView, self).get_context_data(**kwargs)
        return self._build_context_data(context, details, versions, paths)

    def _get_filtered_queryset(self):
        """Filter results based on URL parameters / user access."""
        queryset = self.filter_inventoryitem_by_group(
            self.object.inventoryitem_set)
        return queryset

    def _get_unique_items(self, details):
        """Use optimized DB methods for getting unique items if possible."""
        # if is_postgres():
        #     versions = self.object.inventoryitem_set.distinct("version")
        #     paths = self.object.inventoryitem_set.distinct("path")
        # else:
        details = details.values()
        versions = {item["version"] for item in details}
        paths = {item["path"] for item in details}

        return (versions, paths)

    def _build_context_data(self, context, details, versions, paths):
        # Get list of dicts of installed versions and number of installs
        # for each.
        context["versions"] = [
            {"version": version,
             "count": details.filter(version=version).count()} for
            version in versions]
        # Get list of dicts of installation locations and number of
        # installs for each.
        context["paths"] = [
            {"path": path, "count": details.filter(path=path).count()}
            for path in paths]
        # Get the total number of installations.
        context["install_count"] = details.count()
        # Add in access data.
        context["group_type"] = self.kwargs["group_type"]
        context["group_id"] = self.kwargs["group_id"]
        context["group_name"] = (self.group_instance.name if hasattr(
            self.group_instance, "name") else None)

        return context


@class_login_required
@class_access_required
class CSVExportView(CSVResponseMixin, GroupMixin, View):
    model = InventoryItem

    def get(self, request, *args, **kwargs):
        # Filter data by access level
        queryset = self.filter_inventoryitem_by_group(self.model.objects)

        if kwargs["application_id"] == "0":
            self.set_header(
                ["Name", "BundleID", "BundleName", "Install Count"])
            self.components = ["application", "list", "for",
                               self.kwargs["group_type"]]
            if self.kwargs["group_type"] != "all":
                self.components.append(self.kwargs["group_id"])

            # TODO: Not tested on postgres.
            if is_postgres():
                apps = [self.get_application_entry(item, queryset)
                        for item in
                        queryset.select_related("application").distinct(
                            "application")]
            else:
                apps = {self.get_application_entry(item, queryset)
                        for item in queryset.select_related("application")}

            data = sorted(apps, key=lambda x: x[0])
        else:
            # Inventory List for one application.
            self.set_header(
                ["Hostname", "Serial Number", "Last Checkin", "Console User"])
            self.components = ["application", self.kwargs["application_id"],
                               "for", self.kwargs["group_type"]]
            if self.kwargs["group_type"] != "all":
                self.components.append(self.kwargs["group_id"])
            if self.kwargs["field_type"] != "all":
                self.components.extend(
                    ["where", self.kwargs["field_type"], "is",
                     quote(self.kwargs["field_value"])])

            queryset = queryset.filter(application=kwargs["application_id"])
            if kwargs["field_type"] == "path":
                queryset = queryset.filter(
                    path=kwargs["field_value"])
            elif kwargs["field_type"] == "version":
                queryset = queryset.filter(
                    version=kwargs["field_value"])

            data = [self.get_machine_entry(item, queryset)
                    for item in queryset.select_related("machine")]

        return self.render_to_csv(data)

    def get_application_entry(self, item, queryset):
        # We return tuples, as mutable types are not hashable.
        return (item.application.name,
                item.application.bundleid,
                item.application.bundlename,
                queryset.filter(application=item.application).count())

    def get_machine_entry(self, item, queryset):
        # We return tuples, as mutable types are not hashable.
        return (item.machine.hostname,
                item.machine.serial,
                item.machine.last_checkin,
                item.machine.console_user)


@csrf_exempt
def inventory_submit(request):
    if request.method != 'POST':
        return HttpResponseNotFound('No POST data sent')

    # list of bundleids to ignore
    bundleid_ignorelist = [
        'com.apple.print.PrinterProxy'
    ]
    submission = request.POST
    serial = submission.get('serial')
    machine = None
    if serial:
        try:
            machine = Machine.objects.get(serial=serial)
        except Machine.DoesNotExist:
            return HttpResponseNotFound('Serial Number not found')

        compressed_inventory = submission.get('base64bz2inventory')
        if compressed_inventory:
            compressed_inventory = compressed_inventory.replace(" ", "+")
            inventory_str = utils.decode_to_string(compressed_inventory)
            try:
                inventory_list = plistlib.readPlistFromString(inventory_str)
            except Exception:
                inventory_list = None
            if inventory_list:
                try:
                    inventory_meta = Inventory.objects.get(machine=machine)
                except Inventory.DoesNotExist:
                    inventory_meta = Inventory(machine=machine)
                inventory_meta.sha256hash = \
                    hashlib.sha256(inventory_str).hexdigest()
                # clear existing inventoryitems
                machine.inventoryitem_set.all().delete()
                # insert current inventory items
                for item in inventory_list:
                    app, _ = Application.objects.get_or_create(
                        bundleid=item.get("bundleid", ""),
                        name=item.get("name", ""),
                        bundlename=item.get("CFBundleName", ""))
                    # print app.name
                    # skip items in bundleid_ignorelist.
                    if not item.get('bundleid') in bundleid_ignorelist:
                        i_item = machine.inventoryitem_set.create(
                            application=app, version=item.get("version", ""),
                            path=item.get('path', ''))
                machine.last_inventory_update = datetime.now()
                inventory_meta.save()
            machine.save()
            return HttpResponse(
                "Inventory submmitted for %s.\n" %
                submission.get('serial'))

    return HttpResponse("No inventory submitted.\n")


@csrf_exempt
def inventory_hash(request, serial):
    sha256hash = ""
    machine = None
    if serial:
        try:
            machine = Machine.objects.get(serial=serial)
            inventory_meta = Inventory.objects.get(machine=machine)
            sha256hash = inventory_meta.sha256hash
        except (Machine.DoesNotExist, Inventory.DoesNotExist):
            pass
    else:
        return HttpResponse("MACHINE NOT FOUND")
    return HttpResponse(sha256hash)


def is_postgres():
    postgres_backend = 'django.db.backends.postgresql_psycopg2'
    db_setting = settings.DATABASES['default']['ENGINE']
    return db_setting == postgres_backend
