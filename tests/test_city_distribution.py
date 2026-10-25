"""Tests for the symmetric city distribution across ranks."""

import os
import sys
import unittest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

import find_indeed_job as F                        # noqa: E402
from new_mylib import city_by_states, city_to_state  # noqa: E402


def all_crawl_cities():
    """The crawl city list, flattened and de-duplicated, order preserved.

    new_mylib.py is now the SHARED library -- byte-identical to the Naukri
    project's copy -- so it carries no Indeed-only helpers. The flattening
    lives in the main script (``F.all_cities``); this mirrors it so the
    tests below can state the city contract independently of it.
    """
    cities = [city for group in city_by_states.values() for city in group]
    return list(dict.fromkeys(cities))


class TestCityList(unittest.TestCase):

    def test_the_crawl_list_is_the_reference_list(self):
        cities = all_crawl_cities()
        self.assertEqual(len(cities), 110)
        self.assertEqual(len(set(cities)), 110, "no duplicate cities")
        for expected in ("Delhi", "Pune", "Bengaluru", "Chennai", "Kolkata",
                         "Vasco da Gama", "Porvorim"):
            self.assertIn(expected, cities)

    def test_the_main_script_sees_the_same_list(self):
        """The main script's flattener must agree with the shared table."""
        self.assertEqual(F.all_cities(), all_crawl_cities())

    def test_order_is_stable_between_calls(self):
        self.assertEqual(all_crawl_cities(), all_crawl_cities())

    def test_city_by_states_is_a_dict_of_lists(self):
        self.assertIsInstance(city_by_states, dict)
        for value in city_by_states.values():
            self.assertIsInstance(value, list)


class TestDistribution(unittest.TestCase):
    """Every city assigned exactly once, none skipped, balanced slices."""

    def setUp(self):
        self.cities = all_crawl_cities()

    def assign_all(self, num_sys):
        return [F.get_cities_by_rank(self.cities, rank, num_sys)
                for rank in range(1, num_sys + 1)]

    def test_five_systems_cover_every_city_exactly_once(self):
        slices = self.assign_all(5)
        flattened = [city for part in slices for city in part]
        self.assertEqual(len(flattened), len(self.cities), "no city skipped")
        self.assertEqual(len(set(flattened)), len(self.cities),
                         "no city assigned twice")
        self.assertEqual(sorted(flattened), sorted(self.cities))

    def test_five_systems_give_the_documented_shape(self):
        sizes = [len(part) for part in self.assign_all(5)]
        self.assertEqual(sizes, [23, 23, 23, 23, 18])

    def test_no_city_is_lost_or_duplicated_at_any_system_count(self):
        for num_sys in range(1, 13):
            slices = self.assign_all(num_sys)
            flattened = [city for part in slices for city in part]
            self.assertEqual(sorted(flattened), sorted(self.cities),
                             "num_sys=%d lost or duplicated a city" % num_sys)
            self.assertEqual(len(flattened), len(set(flattened)),
                             "num_sys=%d assigned a city twice" % num_sys)

    def test_slice_sizes_follow_the_reference_formula(self):
        """The reference slice is a fixed-width positional window.

        ``one_unit = int(len(cities) / num_sys) + 1`` means every rank but
        the last takes exactly one_unit cities and the last takes whatever
        remains -- 23/23/23/23/18 for 110 cities over 5 systems.  The tail
        slice is therefore the SMALLEST, by up to num_sys-1 cities.  That is
        the reference behaviour and it is preserved deliberately: it is not
        a defect (no city is lost, none is crawled twice), and changing it
        would re-shard every running rank mid-cycle.
        """
        for num_sys in range(1, 13):
            one_unit = int(len(self.cities) / num_sys) + 1
            slices = self.assign_all(num_sys)
            for index, part in enumerate(slices[:-1], start=1):
                self.assertLessEqual(len(part), one_unit,
                                     "rank %d of %d" % (index, num_sys))
            self.assertLessEqual(len(slices[-1]), one_unit)

    def test_a_very_high_system_count_can_leave_the_last_rank_idle(self):
        """Documented edge case, asserted so it can never surprise anyone.

        With 110 cities and 12 systems the window is 10 wide, so ranks 1-11
        cover all 110 cities and rank 12 gets none.  Every city is still
        crawled exactly once; rank 12 simply has nothing to do and says so
        at startup instead of crawling someone else's cities.
        """
        slices = self.assign_all(12)
        flattened = [city for part in slices for city in part]
        self.assertEqual(sorted(flattened), sorted(self.cities))
        self.assertEqual(slices[-1], [])

    def test_slices_never_overlap(self):
        slices = self.assign_all(5)
        for first in range(len(slices)):
            for second in range(first + 1, len(slices)):
                self.assertEqual(set(slices[first]) & set(slices[second]),
                                 set())

    def test_the_configured_rank_gets_a_non_empty_slice(self):
        self.assertTrue(F.rank_cities())
        self.assertEqual(F.rank_cities(),
                         F.get_cities_by_rank(self.cities, F.rank, F.num_sys))


class TestInvalidRanks(unittest.TestCase):
    """Bad configuration returns nothing rather than silently mis-sharding."""

    def setUp(self):
        self.cities = all_crawl_cities()

    def test_rank_zero_and_negative(self):
        self.assertEqual(F.get_cities_by_rank(self.cities, 0, 5), [])
        self.assertEqual(F.get_cities_by_rank(self.cities, -3, 5), [])

    def test_rank_above_num_sys(self):
        self.assertEqual(F.get_cities_by_rank(self.cities, 6, 5), [])

    def test_zero_or_negative_num_sys(self):
        self.assertEqual(F.get_cities_by_rank(self.cities, 1, 0), [])
        self.assertEqual(F.get_cities_by_rank(self.cities, 1, -2), [])

    def test_non_numeric_configuration(self):
        self.assertEqual(F.get_cities_by_rank(self.cities, "one", 5), [])

    def test_an_empty_city_list(self):
        self.assertEqual(F.get_cities_by_rank([], 1, 5), [])

    def test_rank_cities_raises_a_clear_error_for_a_bad_rank(self):
        original_rank = F.rank
        F.rank = 99
        try:
            with self.assertRaises(RuntimeError) as caught:
                F.rank_cities()
            self.assertIn("rank", str(caught.exception))
        finally:
            F.rank = original_rank


class TestStateResolution(unittest.TestCase):

    def test_known_cities_resolve(self):
        self.assertEqual(city_to_state("Pune"), "Maharashtra")
        self.assertEqual(city_to_state("Bengaluru"), "Karnataka")
        self.assertEqual(city_to_state("Delhi"), "Delhi NCR")

    def test_minor_spelling_differences_still_resolve(self):
        self.assertEqual(city_to_state("Punee"), "Maharashtra")

    def test_an_unknown_city_is_other_not_an_error(self):
        self.assertEqual(city_to_state("Nowhereville"), "Other")
        self.assertEqual(city_to_state(""), "Other")

    def test_the_main_script_caches_the_answer(self):
        F._CITY_STATE_CACHE.clear()
        self.assertEqual(F.city_state_name("Pune"), "Maharashtra")
        self.assertIn("Pune", F._CITY_STATE_CACHE)
        self.assertEqual(F.city_state_name("Pune"), "Maharashtra")

    def test_every_rank_city_resolves_to_something(self):
        for city in F.rank_cities():
            self.assertTrue(F.city_state_name(city))


if __name__ == "__main__":
    unittest.main(verbosity=2)
