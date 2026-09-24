import csv
import tempfile
import unittest
from pathlib import Path

from app.repository import DatasetRepository


def write_csv(path: Path, name: str, rows: list[dict[str, str]]) -> None:
    with (path / name).open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


class RepositoryTests(unittest.TestCase):
    def test_card_mapping_is_stable_and_case_pack_resolves(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            write_csv(root, "transactions.csv", [
                {"TransactionID":"1","TransactionAmt":"10","ProductCD":"C","customer_id":"C1","ts":"2016-07-02 00:00:00","channel":"online","risk_score":"0.2","addr1":"1","addr2":"87","P_emaildomain":"a","R_emaildomain":"b","card1":"1","card2":"","card3":"","card4":"visa","card5":"1","card6":"credit"},
                {"TransactionID":"2","TransactionAmt":"20","ProductCD":"C","customer_id":"C1","ts":"2016-07-02 01:00:00","channel":"online","risk_score":"0.2","addr1":"1","addr2":"87","P_emaildomain":"a","R_emaildomain":"b","card1":"2","card2":"","card3":"","card4":"visa","card5":"1","card6":"debit"},
            ])
            write_csv(root, "identity.csv", [{"TransactionID":"1","id_15":"New","id_23":"","id_30":"Android","id_31":"Chrome","id_33":"100x100","DeviceType":"mobile","DeviceInfo":"Phone"}])
            write_csv(root, "closed_cases_history.csv", [{"case_id":"CC-1","customer_id":"C1","card_id":"C1-K1","opened_at":"2016-07-02 00:00:00","closed_at":"2016-07-02 00:00:00","outcome":"confirmed_fraud","pattern":"card_not_present_fraud","first_fraud_txn_id":"1","txn_ids":"1","n_txns":"1","exposure_usd":"10","connected_card_ids":"","actions_taken":"CREATE_CASE","report_filed":"No","analyst_notes":""}])
            write_csv(root, "case_pack.csv", [{"case_id":"HHG-001","opened_at":"2016-07-02 02:00:00","trigger_type":"risk_score","trigger_text":"Review","flagged_txn_id":"1","card_id":"C1-K1","customer_id":"C1","risk_score":"0.2"}])
            repo = DatasetRepository(root)
            self.assertEqual(repo.transactions["1"].card_id, "C1-K1")
            self.assertEqual(repo.transactions["2"].card_id, "C1-K2")
            self.assertEqual(repo.benchmarks["HHG-001"].flagged_txn_id, "1")


if __name__ == "__main__":
    unittest.main()
