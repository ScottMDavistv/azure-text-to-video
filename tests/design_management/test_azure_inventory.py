import importlib.util
import pathlib
import unittest

PATH = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "azure_readonly_inventory.py"
spec = importlib.util.spec_from_file_location("azure_readonly_inventory", PATH)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

class AzureInventoryTests(unittest.TestCase):
    def test_filter_output_prevents_leak_of_secret_properties(self):
        items = [{"name":"kv-test","type":"Microsoft.KeyVault/vaults","location":"westus",
                  "properties":{"provisioningState":"Succeeded","accessPolicies":["secret"]}},
                 {"name":"other","type":"Microsoft.Compute/virtualMachines","properties":{"secret":"x"}}]
        output = mod.inventory(items, "rg-test")
        self.assertEqual(len(output["resources"]), 1)
        self.assertNotIn("secret", str(output))
        self.assertEqual(output["resources"][0]["provisioning_state"], "Succeeded")
    def test_invalid_resource_group_rejected(self):
        with self.assertRaises(ValueError):
            mod.safe_name("rg-test; rm -rf /")
