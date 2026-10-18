"""Responsibilities: Verify authentication, field boundaries, persistence, and real CSRF for
personal profile maintenance; do not modify production users.
Implementation: Real Django user/session and APIClient; test transaction isolation between two
users; do not mock authentication or database.
Related Modules: api.profile, resumes.html; do not send verification emails, do not call models.

Declaration Index:
- ProfileTests: Boundary tests for basic profile interface.
- ProfileTests.setUp: Create two isolated users, log in one.
- ProfileTests.test_get_and_patch_only_self: Public fields restricted; partial modifications affect
  only self.
- ProfileTests.test_invalid_fields_do_not_partially_save: Invalid format/identity/permission fields
  cause complete rejection, no partial
  writes.
- ProfileTests.test_session_and_csrf_required: Anonymous or CSRF-missing write requests rejected.

Variable Index:
None
"""
from django.contrib.auth import get_user_model
from django.test import TestCase
from rest_framework.test import APIClient


class ProfileTests(TestCase):
    """Function: Validate real profile contract; Logic: Self-isolation and request protection;
    Constraint: Do not prove email ownership verification.
    """

    def setUp(self):
        """No external input; create isolated users and sessions, save objects for comparison to
        ensure other user remains unmodified.
        """
        self.owner = get_user_model().objects.create_user(username="owner", password="a")
        self.other = get_user_model().objects.create_user(username="other", password="b")
        self.client = APIClient()
        self.client.force_login(self.owner)

    def test_get_and_patch_only_self(self):
        """Real GET without password/permissions; PATCH updates self and allows explicit clearing of
        email, does not change account or other user.
        """
        response = self.client.get("/api/profile/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            set(response.data), {"id", "username", "first_name", "email", "date_joined"}
        )
        self.assertEqual(response["Cache-Control"], "no-store, private")
        response = self.client.patch(
            "/api/profile/", {"first_name": "姓名", "email": "owner@example.test"}, format="json"
        )
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.other.refresh_from_db()
        self.assertEqual(self.owner.first_name, "姓名")
        self.assertEqual(self.owner.email, "owner@example.test")
        self.assertEqual(self.other.first_name, "")
        self.assertEqual(self.owner.username, "owner")
        response = self.client.patch("/api/profile/", {"email": ""}, format="json")
        self.assertEqual(response.status_code, 200)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.email, "")
        self.assertEqual(self.owner.first_name, "姓名")

    def test_invalid_fields_do_not_partially_save(self):
        """Invalid email, overly long name, and non-editable fields are rejected entirely;
        permission fields cannot be written or silently ignored.
        """
        for data in (
            {"first_name": "Must not save", "email": "invalid"},
            {"first_name": "x" * 151},
            {"id": self.other.pk},
            {"username": "changed"},
            {"is_staff": True},
        ):
            response = self.client.patch("/api/profile/", data, format="json")
            self.assertEqual(response.status_code, 400)
        self.owner.refresh_from_db()
        self.assertEqual(self.owner.first_name, "")
        self.assertFalse(self.owner.is_staff)

    def test_session_and_csrf_required(self):
        """Anonymous GET/PATCH rejected; real session missing token rejected; only personal center
        token enables self-modification.
        """
        client = APIClient(enforce_csrf_checks=True)
        self.assertEqual(client.get("/api/profile/").status_code, 403)
        self.assertEqual(client.patch("/api/profile/", {}, format="json").status_code, 403)
        client.force_login(self.owner)
        response = client.patch("/api/profile/", {"first_name": "N"}, format="json")
        self.assertEqual(response.status_code, 403)
        client.get("/resumes/")
        response = client.patch(
            "/api/profile/", {"first_name": "N"}, format="json",
            HTTP_X_CSRFTOKEN=client.cookies["csrftoken"].value,
        )
        self.assertEqual(response.status_code, 200)
