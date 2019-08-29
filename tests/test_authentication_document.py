from collections import defaultdict
import json
from nose.tools import (
    assert_raises_regexp,
    assert_raises,
    eq_,
    set_trace,
)
from sqlalchemy.orm.exc import (
    MultipleResultsFound,
    NoResultFound,
)
from authentication_document import AuthenticationDocument
from . import DatabaseTest
from model import (
    Audience,
    Place,
    ServiceArea,
)
from util.problem_detail import ProblemDetail
from problem_details import INVALID_INTEGRATION_DOCUMENT
from testing import MockPlace

# Alias for a long class name
AuthDoc = AuthenticationDocument

class TestParseCoverage(DatabaseTest):

    EVERYWHERE = AuthenticationDocument.COVERAGE_EVERYWHERE

    def parse_places(self, coverage_object, expected_places=None,
                     expected_unknown=None, expected_ambiguous=None):
        """Call AuthenticationDocument.parse_coverage. Verify that the parsed
        list of places, as well as the dictionaries of unknown and
        ambiguous place names, are as expected.
        """
        place_objs, unknown, ambiguous = AuthDoc.parse_coverage(
            self._db, coverage_object, MockPlace
        )
        empty = defaultdict(list)
        expected_places = expected_places or []
        expected_unknown = expected_unknown or empty
        expected_ambiguous = expected_ambiguous or empty
        # TODO PYTHON3 replace eq_sorted() with eq_()
        def eq_sorted(a, b):
            def key(x):
                return id(x)
            eq_(sorted(a, key=key), sorted(b, key=key))
        eq_sorted(expected_places, place_objs)
        eq_sorted(expected_unknown, unknown)
        eq_sorted(expected_ambiguous, ambiguous)

    def test_universal_coverage(self):
        # Test an authentication document that says a library covers the
        # whole universe.
        self.parse_places(
            self.EVERYWHERE, [MockPlace.EVERYWHERE]
        )

    def test_entire_country(self):
        # Test an authentication document that says a library covers an
        # entire country.
        us = MockPlace()
        MockPlace.by_name["US"] = us
        self.parse_places(
            {"US": self.EVERYWHERE },
            expected_places=[us]
        )

    def test_ambiguous_country(self):
        # Test the unlikely scenario where an authentication document says a
        # library covers an entire country, but it's ambiguous which
        # country is being referred to.

        canada = MockPlace()
        MockPlace.by_name["CA"] = canada
        MockPlace.by_name["Europe I think?"] = MockPlace.AMBIGUOUS
        self.parse_places(
            {"Europe I think?": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=[canada],
            expected_ambiguous={"Europe I think?": self.EVERYWHERE}
        )

    def test_unknown_country(self):
        # Test an authentication document that says a library covers an
        # entire country, but the library registry doesn't know anything about
        # that country's geography.

        canada = MockPlace()
        MockPlace.by_name["CA"] = canada
        self.parse_places(
            {"Memory Alpha": self.EVERYWHERE, "CA": self.EVERYWHERE },
            expected_places=[canada],
            expected_unknown={"Memory Alpha": self.EVERYWHERE}
        )

    def test_places_within_country(self):
        # Test an authentication document that says a library
        # covers one or more places within a country.

        # This authentication document covers two places called
        # "San Francisco" (one in the US and one in Mexico) as well as a
        # place called "Mexico City" in Mexico.
        #
        # Note that it's invalid to map a country name to a single
        # place name (it's supposed to always be a list), but our
        # parser can handle it.
        doc = {"US": "San Francisco", "MX": ["San Francisco", "Mexico City"]}

        place1 = MockPlace()
        place2 = MockPlace()
        place3 = MockPlace()
        place4 = MockPlace()
        us = MockPlace(inside={"San Francisco": place1, "San Jose": place2})
        mx = MockPlace(inside={"San Francisco": place3, "Mexico City": place4})
        MockPlace.by_name["US"] = us
        MockPlace.by_name["MX"] = mx

        # AuthenticationDocument.parse_coverage is able to turn those
        # three place names into place objects.
        self.parse_places(
            doc,
            expected_places=[place1, place3, place4]
        )

    def test_ambiguous_place_within_country(self):
        # Test an authentication document that names an ambiguous
        # place within a country.
        us = MockPlace(inside={"Springfield": MockPlace.AMBIGUOUS})
        MockPlace.by_name["US"] = us

        self.parse_places(
            {"US": ["Springfield"]},
            expected_ambiguous={"US": ["Springfield"]}
        )

    def test_unknown_place_within_country(self):
        # Test an authentication document that names an unknown
        # place within a country.
        sf = MockPlace()
        us = MockPlace(inside={"San Francisco": sf})
        MockPlace.by_name["US"] = us

        self.parse_places(
            {"US": "Nowheresville"},
            expected_unknown={"US": ["Nowheresville"]}
        )

    def test_unscoped_place_is_in_default_nation(self):
        # Test an authentication document that names places without
        # saying which nation they're in.
        ca = MockPlace()
        ut = MockPlace()

        # Without a default nation on the server side, we can't make
        # sense of these place names.
        self.parse_places("CA", expected_unknown={"??": "CA"})

        self.parse_places(
            ["CA", "UT"], expected_unknown={"??": ["CA", "UT"]}
        )

        us = MockPlace(inside={"CA": ca, "UT": ut})
        us.abbreviated_name = "US"
        MockPlace.by_name["US"] = us

        # With a default nation in place, a bare string like "CA"
        # is treated the same as a correctly formatted dictionary
        # like {"US": ["CA"]}
        MockPlace._default_nation = us
        self.parse_places("CA", expected_places=[ca])
        self.parse_places(["CA", "UT"], expected_places=[ca, ut])

        MockPlace._default_nation = None


class TestLinkExtractor(object):
    """Test the _extract_link helper method."""

    def test_no_matching_link(self):
        links = [dict(rel="alternate", href="http://foo/", type="text/html")]

        # There is no link with the given relation.
        eq_(None, AuthDoc._extract_link(links, rel='self'))

        # There is a link with the given relation, but the type is wrong.
        eq_(
            None,
            AuthDoc._extract_link(
                links, 'alternate', require_type="text/plain"
            )
        )


    def test_prefer_type(self):
        """Test that prefer_type holds out for the link you're
        looking for.
        """
        first_link = dict(
            rel="alternate", href="http://foo/", type="text/html"
        )
        second_link = dict(
            rel="alternate", href="http://bar/",
            type="text/plain;charset=utf-8"
        )
        links = [first_link, second_link]

        # We would prefer the second link.
        eq_(second_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="text/plain"
            )
        )

        # We would prefer the first link.
        eq_(first_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="text/html"
            )
        )

        # The type we prefer is not available, so we get the first link.
        eq_(first_link,
            AuthDoc._extract_link(
                links, 'alternate', prefer_type="application/xhtml+xml"
            )
        )

    def test_empty_document(self):
        """Provide an empty Authentication For OPDS document to test
        default values.
        """
        place = MockPlace()
        everywhere = place.everywhere(None)
        parsed = AuthDoc.from_string(None, "{}", place)

        # In the absence of specific information, it's assumed the
        # OPDS server is open to everyone.
        eq_(([everywhere], {}, {}), parsed.service_area)
        eq_(([everywhere], {}, {}), parsed.focus_area)
        eq_([parsed.PUBLIC_AUDIENCE], parsed.audiences)

        eq_(None, parsed.id)
        eq_(None, parsed.title)
        eq_(None, parsed.service_description)
        eq_(None, parsed.color_scheme)
        eq_(None, parsed.collection_size)
        eq_(None, parsed.public_key)
        eq_(None, parsed.website)
        eq_(False, parsed.online_registration)
        eq_(None, parsed.root)
        eq_([], parsed.links)
        eq_(None, parsed.logo)
        eq_(None, parsed.logo_link)
        eq_(False, parsed.anonymous_access)

    def test_real_document(self):
        """Test an Authentication For OPDS document that demonstrates
        most of the features we're looking for.
        """
        document = {
            "id": "http://library/authentication-for-opds-file",
            "title": "Ansonia Public Library",
            "links": [
                {"rel": "logo", "href": "data:image/png;base64,some-image-data", "type": "image/png"},
                {"rel": "alternate", "href": "http://ansonialibrary.org", "type": "text/html"},
                {"rel": "register", "href": "http://example.com/get-a-card/", "type": "text/html"},
                {"rel": "start", "href": "http://catalog.example.com/", "type": "text/html/"},
                {"rel": "start", "href": "http://opds.example.com/", "type": "application/atom+xml;profile=opds-catalog"}
            ],
            "service_description": "Serving Ansonia, CT",
            "color_scheme": "gold",
            "collection_size": {"eng": 100, "spa": 20},
            "public_key": "a public key",
            "features": {"disabled": [], "enabled": ["https://librarysimplified.org/rel/policy/reservations"]},
            "authentication": [
                {
                    "type": "http://opds-spec.org/auth/basic",
                    "description": "Log in with your library barcode",
                    "inputs": {"login": {"keyboard": "Default"},
                               "password": {"keyboard": "Default"}},
                    "labels": {"login": "Barcode", "password": "PIN"}
                }
            ]
        }

        place = MockPlace()
        everywhere = place.everywhere(None)
        parsed = AuthDoc.from_dict(None, document, place)

        # Information about the OPDS server has been extracted from
        # JSON and put into the AuthenticationDocument object.
        eq_("http://library/authentication-for-opds-file", parsed.id)
        eq_("Ansonia Public Library", parsed.title)
        eq_("Serving Ansonia, CT", parsed.service_description)
        eq_("gold", parsed.color_scheme)
        eq_({"eng": 100, "spa": 20}, parsed.collection_size)
        eq_("a public key", parsed.public_key)
        eq_({u'rel': 'alternate', u'href': u'http://ansonialibrary.org',
             u'type': u'text/html'},
            parsed.website)
        eq_(True, parsed.online_registration)
        eq_({"rel": "start", "href": "http://opds.example.com/", "type": "application/atom+xml;profile=opds-catalog"}, parsed.root)
        eq_("data:image/png;base64,some-image-data", parsed.logo)
        eq_(None, parsed.logo_link)
        eq_(False, parsed.anonymous_access)

    def online_registration_for_one_authentication_mechanism(self):
        """An OPDS server offers online registration if _any_ of its
        authentication flows offer online registration.

        It also works if the server itself offers registration (see
        previous test).
        """
        document = {
            "authentication": [
                {
                    "description": "You'll never guess the secret code.",
                    "type": "http://opds-spec.org/auth/basic"
                },
                {
                    "description": "But anyone can get a library card.",
                    "type": "http://opds-spec.org/auth/basic",
                    "links": [
                        { "rel": "register",
                          "href": "http://get-a-library-card/"
                        }
                    ]
                }
            ]
        }
        eq_(True, parsed.online_registration)


    def test_name_treated_as_title(self):
        """Some invalid documents put the library name in 'name' instead of title.
        We can handle these documents.
        """
        document = dict(name="My library")
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_("My library", auth.title)

    def test_logo_link(self):
        """You can link to your logo, instead of including it in the
        document.
        """
        document = {
            "links": [
                dict(rel="logo", href="http://logo.com/logo.jpg")
            ]
        }
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(None, auth.logo)
        eq_({"href": "http://logo.com/logo.jpg", "rel": "logo"}, auth.logo_link)

    def test_audiences(self):
        """You can specify the target audiences for your OPDS server."""
        document = {"audience": ["educational-secondary", "research"]}
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(["educational-secondary", "research"], auth.audiences)

    def test_anonymous_access(self):
        """You can signal that your OPDS server allows anonymous access by
        including it as an authentication type.
        """
        document = dict(authentication=[
            dict(type="http://opds-spec.org/auth/basic"),
            dict(type="https://librarysimplified.org/rel/auth/anonymous")
        ])
        auth = AuthDoc.from_dict(None, document, MockPlace())
        eq_(True, auth.anonymous_access)


class TestUpdateServiceAreas(DatabaseTest):

    def test_set_service_areas(self):
        # Test the method that replaces a Library's ServiceAreas.
        m = AuthenticationDocument.set_service_areas

        library = self._library()
        p1 = self._place()
        p2 = self._place()

        def eligibility_areas():
            return [x.place for x in library.service_areas
                    if x.type==ServiceArea.ELIGIBILITY]

        def focus_areas():
            return [x.place for x in library.service_areas
                    if x.type==ServiceArea.FOCUS]

        # Try a successful case.
        p1_only = [[p1], {}, {}]
        p2_only = [[p2], {}, {}]
        m(library, p1_only, p2_only)
        eq_([p1], eligibility_areas())
        eq_([p2], focus_areas())

        # If you pass in two empty inputs, no changes are made.
        empty = [[], {}, {}]
        m(library, empty, empty)
        eq_([p1], eligibility_areas())
        eq_([p2], focus_areas())

        # If you pass only one value, the focus area is set to that
        # value and the eligibility area is cleared out.
        m(library, p1_only, empty)
        eq_([], eligibility_areas())
        eq_([p1], focus_areas())

        m(library, empty, p2_only)
        eq_([], eligibility_areas())
        eq_([p2], focus_areas())


    def test_known_place_becomes_servicearea(self):
        """Test the helper method in a successful case."""
        library = self._library()

        # We identified two places, with no ambiguous or unknown
        # places.
        p1 = self._place()
        p2 = self._place()
        valid = [p1, p2]
        ambiguous = []
        unknown = []

        areas = []

        # This will use those places to create new ServiceAreas,
        # which will be gathered in the 'areas' array.
        problem = AuthenticationDocument._update_service_areas(
            library, [valid, unknown, ambiguous], ServiceArea.FOCUS,
            areas
        )
        eq_(None, problem)

        [a1, a2] = sorted(library.service_areas, key = lambda x: x.place_id)
        eq_(p1, a1.place)
        eq_(ServiceArea.FOCUS, a1.type)

        eq_(p2, a2.place)
        eq_(ServiceArea.FOCUS, a2.type)

        # The ServiceArea IDs were added to the `ids` list.
        eq_(set([a1, a2]), set(areas))


    def test_ambiguous_and_unknown_places_become_problemdetail(self):
        """Test the helper method in a case that ends in failure."""
        library = self._library()

        # We were able to identify one valid place.
        valid = [self._place()]

        # But we also found unknown and ambiguous places.
        ambiguous = ["Ambiguous"]
        unknown = ["Unknown 1", "Unknown 2"]

        ids = []
        problem = AuthenticationDocument._update_service_areas(
            library, [valid, unknown, ambiguous], ServiceArea.ELIGIBILITY,
            ids
        )

        # We got a ProblemDetail explaining the problem
        assert isinstance(problem, ProblemDetail)
        eq_(INVALID_INTEGRATION_DOCUMENT.uri, problem.uri)
        eq_(
            'The following service area was unknown: ["Unknown 1", "Unknown 2"]. The following service area was ambiguous: ["Ambiguous"].',
            problem.detail
        )

        # No IDs were added to the list.
        eq_([], ids)

    def test_update_service_areas(self):

        # This Library has no ServiceAreas associated with it.
        library = self._library()

        country1 = self._place(abbreviated_name="C1", type=Place.NATION)
        country2 = self._place(abbreviated_name="C2", type=Place.NATION)

        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(
            service_area=everywhere,
            focus_area = { country1.abbreviated_name : everywhere,
                           country2.abbreviated_name : everywhere }
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # Now this Library has three associated ServiceAreas.
        [a1, a2, a3] = sorted(
            [(x.type, x.place.abbreviated_name)
             for x in library.service_areas]
        )
        everywhere_place = Place.everywhere(self._db)

        # Anyone is eligible for access.
        eq_(('eligibility', everywhere_place.abbreviated_name), a1)

        # But people in two particular countries are the focus.
        eq_(('focus', country1.abbreviated_name), a2)
        eq_(('focus', country2.abbreviated_name), a3)

        # Remove one of the countries from the focus, add a new one,
        # and try again.
        country3 = self._place(abbreviated_name="C3", type=Place.NATION)
        doc_dict = dict(
            service_area=everywhere,
            focus_area = { country1.abbreviated_name : everywhere,
                           country3.abbreviated_name : everywhere }
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        doc.update_service_areas(library)
        self._db.commit()

        # The ServiceArea for country #2 has been removed.
        assert a2 not in library.service_areas
        assert not any(a.place == country2 for a in library.service_areas)

        [a1, a2, a3] = sorted(
            [(x.type, x.place.abbreviated_name)
             for x in library.service_areas]
        )
        eq_(('eligibility', everywhere_place.abbreviated_name), a1)
        eq_(('focus', country1.abbreviated_name), a2)
        eq_(('focus', country3.abbreviated_name), a3)

    def test_service_area_registered_as_focus_area_if_no_focus_area(self):

        library = self._library()
        # Create an authentication document that defines service_area
        # but not focus_area.
        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(service_area=everywhere)
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # We have a focus area but no explicit eligibility area. This
        # means that the library's eligibility area and focus area are
        # the same.
        [area] = library.service_areas
        eq_(Place.EVERYWHERE, area.place.type)
        eq_(ServiceArea.FOCUS, area.type)


    def test_service_area_registered_as_focus_area_if_identical_to_focus_area(self):
        library = self._library()

        # Create an authentication document that defines service_area
        # and focus_area as the same value.
        everywhere = AuthenticationDocument.COVERAGE_EVERYWHERE
        doc_dict = dict(
            service_area=everywhere,
            focus_area=everywhere,
        )
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_service_areas(library)
        self._db.commit()
        eq_(None, problem)

        # Since focus area and eligibility area are the same, only the
        # focus area was registered.
        [area] = library.service_areas
        eq_(Place.EVERYWHERE, area.place.type)
        eq_(ServiceArea.FOCUS, area.type)


class TestUpdateAudiences(DatabaseTest):

    def setup(self):
        super(TestUpdateAudiences, self).setup()
        self.library = self._library()

    def update(self, audiences):
        """Wrapper around AuthenticationDocument._update_audiences."""
        result = AuthenticationDocument._update_audiences(
            self.library, audiences
        )

        # If there's a problem detail document, it must be of the type
        # INVALID_INTEGRATION_DOCUMENT. The caller may perform additional
        # checks.
        if isinstance(result, ProblemDetail):
            eq_(result.uri, INVALID_INTEGRATION_DOCUMENT.uri)
        return result

    def test_update_audiences(self):

        # Set the library's audiences.
        audiences = [Audience.EDUCATIONAL_SECONDARY, Audience.RESEARCH]
        doc_dict = dict(audience=audiences)
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_audiences(self.library)
        eq_(None, problem)
        eq_(set(audiences), set([x.name for x in self.library.audiences]))

        # Set them again to different but partially overlapping values.
        audiences = [
            Audience.EDUCATIONAL_PRIMARY, Audience.EDUCATIONAL_SECONDARY
        ]
        problem = self.update(audiences)
        eq_(set(audiences), set([x.name for x in self.library.audiences]))

    def test_update_audiences_to_invalid_value(self):
        # You're not supposed to specify a single string as `audience`,
        # but we can handle it.
        audience = Audience.EDUCATIONAL_PRIMARY
        problem = self.update(audience)
        eq_([audience], [x.name for x in self.library.audiences])

        # But you can't specify some other random object.
        value = dict(k="v")
        problem = self.update(value)
        eq_(u"'audience' must be a list: %r" % value, problem.detail)

    def test_unrecognized_audiences_become_other(self):
        # If you specify an audience that we don't recognize, it becomes
        # Audience.OTHER.
        audiences = ["Some random audience", Audience.PUBLIC]
        self.update(audiences)
        eq_(set([Audience.OTHER, Audience.PUBLIC]),
            set([x.name for x in self.library.audiences]))

    def test_audience_defaults_to_public(self):
        # If a library doesn't specify its audience, we assume it's open
        # to the general public.
        self.update(None)
        eq_([Audience.PUBLIC], [x.name for x in self.library.audiences])


class TestUpdateCollectionSize(DatabaseTest):

    def setup(self):
        super(TestUpdateCollectionSize, self).setup()
        self.library = self._library()

    def update(self, value):
        result = AuthenticationDocument._update_collection_size(
            self.library, value
        )
        # If there's a problem detail document, it must be of the type
        # INVALID_INTEGRATION_DOCUMENT. The caller may perform additional
        # checks.
        if isinstance(result, ProblemDetail):
            eq_(result.uri, INVALID_INTEGRATION_DOCUMENT.uri)
        return result

    def test_success(self):
        sizes = dict(eng=100, jpn=0)
        doc_dict = dict(collection_size=sizes)
        doc = AuthenticationDocument.from_dict(self._db, doc_dict)
        problem = doc.update_collection_size(self.library)
        eq_(None, problem)

        # Two CollectionSummaries have been created, for the English
        # collection and the (empty) Japanese collection.
        eq_([(u'eng', 100), (u'jpn', 0)],
            sorted([(x.language, x.size) for x in self.library.collections]))

        # Update the library with new data.
        self.update({"eng": "200"})
        # The Japanese collection has been removed altogether, since
        # it was not mentioned in the input.
        [english] = self.library.collections
        eq_("eng", english.language)
        eq_(200, english.size)

        self.update(None)
        # Now both collections have been removed.
        eq_([], self.library.collections)

    def test_single_collection(self):
        # Register a single collection not differentiated by language.
        self.update(100)

        [unknown] = self.library.collections
        eq_(None, unknown.language)
        eq_(100, unknown.size)

        # A string will also work.
        self.update("51")

        [unknown] = self.library.collections
        eq_(None, unknown.language)
        eq_(51, unknown.size)

    def test_unknown_language_registered_as_unknown(self):
        self.update(dict(mmmmm=100))
        [unknown] = self.library.collections
        eq_(None, unknown.language)
        eq_(100, unknown.size)

        # Here's a tricky case with multiple unknown languages.  They
        # all get grouped together into a single 'unknown language'
        # collection.
        self.update({None: 100, "mmmmm":200, "zzzzz":300})
        [unknown] = self.library.collections
        eq_(None, unknown.language)
        eq_(100+200+300, unknown.size)

    def test_invalid_collection_size(self):
        problem = self.update([1,2,3])
        eq_("'collection_size' must be a number or an object mapping language codes to numbers", problem.detail)

    def test_negative_collection_size(self):
        problem = self.update(-100)
        eq_("Collection size cannot be negative.", problem.detail)
