"""A single-group audit must not invent an adjacent pitch step."""
import unittest

from scripts.audit_dub import adjacent_step_labels


class StepLabelsTest(unittest.TestCase):
    def test_one_group_has_no_adjacent_pair(self):
        self.assertEqual(adjacent_step_labels([{'group': 7}]), [])

    def test_neighbors_are_labeled_in_order(self):
        self.assertEqual(adjacent_step_labels([{'group': 3}, {'group': 4}, {'group': 5}]),
                         ['3→4', '4→5'])


if __name__ == '__main__':
    unittest.main()
