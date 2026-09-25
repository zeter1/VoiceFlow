from __future__ import annotations

import unittest

from voiceflow_app.composition import ApplicationServices


class FakeService:
    pass


class CompositionTests(unittest.TestCase):
    def test_application_services_accept_headless_ports_without_desktop_imports(self):
        recorder = FakeService()
        transcriber = FakeService()
        cleaner = FakeService()
        notification_factory = lambda root: ("notification", root)
        tray_factory = lambda app: ("tray", app)

        services = ApplicationServices(
            recorder=recorder,
            transcriber=transcriber,
            cleaner=cleaner,
            notification_factory=notification_factory,
            tray_factory=tray_factory,
        )

        self.assertIs(services.recorder, recorder)
        self.assertIs(services.transcriber, transcriber)
        self.assertIs(services.cleaner, cleaner)
        self.assertEqual(services.notification_factory("root"), ("notification", "root"))
        self.assertEqual(services.tray_factory("app"), ("tray", "app"))


if __name__ == "__main__":
    unittest.main()
