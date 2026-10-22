"""Shared data helpers for the independent Indeed job scraper.

WHY THIS FILE EXISTS
--------------------
The Naukri scraper imports its city tables and its city -> state resolver
from its own ``new_mylib.py``.  The Indeed project must be completely
independent, so this is an INDEPENDENT COPY of the parts the crawler
actually uses:

  * ``city_by_states``           -- the 110 crawl cities, exactly the list the
                                    Naukri reference project crawls today.
  * ``city_by_states_original``  -- the 29-state / 905-city lookup table used
                                    only to resolve a city to its state.
  * ``city_to_state()``          -- the resolver, behaviour preserved.
  * ``job_by_department``        -- the keyword plan, narrowed to the 85-role
                                    target-audience plan held in
                                    ``target_audience_roles.py``.

WHAT IS DELIBERATELY NOT COPIED
-------------------------------
The Naukri library also carries text-to-speech, language tables, battery and
internet helpers and a 432-entry department list that the Naukri main script
overrides at import time anyway.  None of that is used by a crawl, so none of
it is reproduced here: this module imports nothing outside the standard
library (``Levenshtein`` is used when installed, with a pure-Python fallback
so the project has no hard dependency on it).

NOTHING IN THIS FILE IMPORTS FROM THE NAUKRI PROJECT.
"""

# ---------------------------------------------------------------------------
# FUZZY MATCHING
# ---------------------------------------------------------------------------
# The reference implementation uses python-Levenshtein.  It is kept when it
# is installed, because it is what produced the verified behaviour, and a
# small pure-Python implementation stands in when it is not, so a fresh
# checkout can resolve states without compiling anything.
try:                                            # pragma: no cover - env dependent
    from Levenshtein import distance as levenshtein_distance
except ImportError:                             # pragma: no cover - env dependent
    def levenshtein_distance(first: str, second: str) -> int:
        """Edit distance between two strings (iterative two-row DP)."""
        if first == second:
            return 0
        if not first:
            return len(second)
        if not second:
            return len(first)

        previous = list(range(len(second) + 1))
        for i, first_char in enumerate(first, start=1):
            current = [i]
            for j, second_char in enumerate(second, start=1):
                current.append(min(
                    previous[j] + 1,                                    # delete
                    current[j - 1] + 1,                                 # insert
                    previous[j - 1] + (first_char != second_char),       # swap
                ))
            previous = current
        return previous[-1]


def is_same(input_word, correct_word, threshold: int = 1) -> bool:
    """True when two names differ by at most ``threshold`` edits.

    Case-insensitive, so "pune" and "Pune" are the same place.
    """
    return levenshtein_distance(
        str(input_word).lower(), str(correct_word).lower()
    ) <= threshold


# ---------------------------------------------------------------------------
# CRAWL CITIES
# ---------------------------------------------------------------------------
# 110 cities in ONE positional list.  ``get_cities_by_rank()`` in
# find_indeed_job.py slices this list, so the ORDER MATTERS: editing it
# re-shards every rank.  Copied unchanged from the reference project.

city_by_states = {
    "Major Cities": ["Delhi", "Gurugram", "Gurgaon", "Noida", "Greater Noida", 
                    "Faridabad", "Chandigarh", "Mohali", "Panchkula", "Bengaluru", 
                    "Bangalore", "Mysuru", "Mysore", "Mangaluru", "Mangalore", 
                    "Hubballi", "Hubli", "Belagavi", "Udupi", "Hyderabad", "Warangal",
                    "Chennai", "Coimbatore", "Madurai", "Tiruchirappalli",
                    "Salem", "Vellore", "Tiruppur", "Erode", "Hosur",
                    "Chengalpattu", "Mumbai", "Pune", "Navi Mumbai", "Thane",
                    "Nagpur", "Nashik", "Aurangabad", "Kolhapur", "Solapur",
                    "Ahmedabad", "Gandhinagar", "Surat", "Vadodara", "Rajkot",
                    "Anand", "Vapi", "Bharuch", "Kochi", "Cochin",
                    "Thiruvananthapuram", "Kozhikode", "Thrissur", "Kottayam",
                    "Ernakulam", "Kollam", "Indore", "Bhopal", "Gwalior",
                    "Jabalpur", "Jaipur", "Jodhpur", "Udaipur", "Kota",
                    "Ajmer", "Lucknow", "Kanpur", "Ghaziabad", "Meerut",
                    "Agra", "Varanasi", "Prayagraj", "Allahabad", "Bareilly",
                    "Kolkata", "Howrah", "Durgapur", "Siliguri", "Kharagpur",
                    "Asansol", "Bhubaneswar", "Cuttack", "Rourkela",
                    "Berhampur", "Visakhapatnam", "Vijayawada", "Guntur",
                    "Tirupati", "Kakinada", "Rajahmundry", "Nellore", "Patna",
                    "Ranchi", "Jamshedpur", "Dhanbad", "Bokaro Steel City",
                    "Raipur", "Bhilai", "Bilaspur", "Dehradun", "Roorkee",
                    "Haridwar", "Guwahati", "Shillong", "Imphal", "Agartala",
                    "Panaji", "Margao", "Vasco da Gama", "Porvorim"],
}

# ---------------------------------------------------------------------------
# CITY -> STATE LOOKUP TABLE
# ---------------------------------------------------------------------------
# Used only by city_to_state().  29 states / union territories, 905 cities.

city_by_states_original = {
    "Delhi NCR": ['Delhi', 'Gurugram', 'Noida', 'Greater Noida',
                  'Ballabgarh', 'Bhiwadi', 'Manesar', 'Jhajjar',
                  'Tauru', 'Khurja'],
    
    "Andhra Pradesh": [
        "Visakhapatnam", "Vijayawada", "Guntur", "Nellore", "Kurnool",
        "Tirupati", "Rajahmundry", "Kakinada", "Kadapa", "Anantapur",
        "Eluru", "Ongole", "Machilipatnam", "Chittoor", "Hindupur",
        "Bhimavaram", "Tadepalligudem", "Proddatur", "Adoni", "Amalapuram",
        "Madanapalle", "Dharmavaram", "Markapur", "Nandyal", "Srikakulam",
        "Rajampet", "Peddapuram", "Rayachoti", "Bapatla",
        "Nellore", "Palnadu", "Kovur", "Tadipatri", "Punganur", "Chilakaluripet",
        "Sattenapalli", "Bobbili", "Peddapalli", "Anakapalle", "Addanki",
        "Chintapalli", "Peddagummadiv", "Brahmanapalli", "Tuni", "Tanuku",
        "Vinukonda", "Amadalavalasa", "Srikalahasti", "Mummidivaram",
    ],
    "Arunachal Pradesh": [
        "Itanagar", "Tawang", "Ziro", "Pasighat", "Roing",
        "Tezu", "Bomdila", "Naharlagun", "Changlang", "Seppa",
        "Yingkiong", "Namsai", "Hawai", "Aalo", "Raga", "Tali", 
        "Joram", "Dirang", "Nirjuli", "Sangdupota", "Koloriang",
    ],
    "Assam": [
        "Guwahati", "Silchar", "Dibrugarh", "Jorhat", "Nagaon",
        "Tinsukia", "Tezpur", "Bongaigaon", "North Lakhimpur",
        "Karimganj", "Goalpara", "Dhubri", "Haflong", "Sibsagar",
        "Sonari", "Nalbari", "Jorhat", "Barpeta", "Hojai", "Bajali",
        "Dibrugarh", "Dhemaji", "Morigaon", "Golaghat", "Kamrup", "Barpeta",
        "Mangaldoi", "Bilasipara", "Lakhimpur", "Charaideo", "Majuli",
        "Moran", "Darrang", "Hailakandi", "Haflong", "Tihu", "Bongaigaon",
    ],
    "Bihar": [
        "Patna", "Gaya", "Bhagalpur", "Muzaffarpur", "Purnia",
        "Darbhanga", "Begusarai", "Ara", "Katihar", "Munger",
        "Chhapra", "Saharsa", "Samastipur", "Bettiah", "Siwan",
        "Motihari", "Kishanganj", "Nalanda", "Buxar", "Nawada",
        "Lakhisarai", "Khagaria", "Sheikhpura", "Jamui", "Jahanabad",
        "Supaul", "Vaishali", "Rohtas", "Aurangabad", "Banka",
        "Bhabhua", "Chapra", "Buxar", "Chhapra", "Dehri", "Rajgir",
        "Patna City", "Phulwari Sharif", "Bihar Sharif",
    ],
    "Chhattisgarh": [
        "Raipur", "Bhilai", "Bilaspur", "Korba", "Durg",
        "Rajnandgaon", "Jagdalpur", "Ambikapur", "Raigarh", "Mahasamund",
        "Kanker", "Dhamtari", "Dalli-Rajhara", "Champa", "Janjgir",
        "Bemetara", "Kondagaon", "Balod", "Raipur City", "Bijapur",
        "Narayanpur", "Balodabazar", "Mungeli", "Surguja", "Jashpur", "Kabirdham",
        "Surajpur", "Korba District", "Sarguja", "Dongargarh", "Kawardha", 
    ],
    "Goa": [
        "Panaji", "Margao", "Vasco da Gama", "Mapusa", "Ponda",
        "Bicholim", "Curchorem", "Sanguem", "Valpoi", "Quepem",
        "Canacona", "Sanquelim", "Cortalim", "Assagao", "Aldona",
        "Baga", "Calangute", "Candolim", "Anjuna", "Colva", "Benaulim",
        "Varca", "Majorda", "Navelim", "Raia", "Mormugao", "Verem",
        "Ribandar", "Sirsaim", "Taleigao", "Porvorim", "Assagao",
    ],
    "Gujarat": [
        "Ahmedabad", "Surat", "Vadodara", "Rajkot", "Bhavnagar",
        "Jamnagar", "Junagadh", "Gandhinagar", "Anand", "Nadiad",
        "Morbi", "Porbandar", "Navsari", "Bharuch", "Patan",
        "Godhra", "Mehsana", "Vapi", "Valsad", "Himmatnagar",
        "Dahod", "Bhuj", "Veraval", "Wankaner", "Gandhinagar",
        "Surendranagar", "Unjha", "Rajpipla", "Visnagar", "Palanpur",
        "Modasa", "Kalol", "Viramgam", "Borsad", "Kheda", "Kadi", 
        "Dholka", "Mansa", "Chhota Udepur", "Dahej", "Udhna", "Ankleshwar", 
        "Navsari", "Tapi", "Daman", "Diu",
    ],
    "Haryana": [
        "Gurgaon", "Faridabad", "Panipat", "Ambala", "Karnal",
        "Sonipat", "Rohtak", "Hisar", "Yamunanagar", "Panchkula",
        "Bhiwani", "Bahadurgarh", "Sirsa", "Jind", "Kaithal",
        "Palwal", "Fatehabad", "Mahendragarh", "Rewari", "Narnaul",
        "Barwala", "Ratia", "Hansi", "Tosham", "Bawani Khera", 
        "Pinjore", "Shahbad", "Nuh", "Samalkha", "Rohat",
    ],
    "Himachal Pradesh": [
        "Shimla", "Manali", "Dharamshala", "Mandi", "Kullu",
        "Chamba", "Solan", "Bilaspur", "Hamirpur", "Una",
        "Palampur", "Nahan", "Paonta Sahib", "Keylong", "Sundernagar",
        "Kangra", "Narkanda", "Kullu", "Bhuntar", "Arki", "Reckong Peo",
        "Jubbal", "Chintpurni", "Tissa", "Nagrota Surian", "Ghumarwin",
    ],
    "Jharkhand": [
        "Ranchi", "Jamshedpur", "Dhanbad", "Bokaro Steel City",
        "Hazaribagh", "Deoghar", "Giridih", "Ramgarh", "Phusro",
        "Chakradharpur", "Gumla", "Lohardaga", "Chaibasa", "Seraikela",
        "Dumka", "Godda", "Pakur", "Koderma", "Simdega", "Latehar",
        "Khunti", "Sahibganj", "Palamu", "Chatra", "Bermo", "Jamtara",
        "Ramgarh", "Madhupur", "Barkagaon", "Mandar", "Tundi", "Tata Nagar",
    ],
    "Karnataka": [
        "Bengaluru", "Mysuru", "Mangaluru", "Hubballi", "Belagavi",
        "Davanagere", "Ballari", "Shivamogga", "Tumakuru", "Udupi",
        "Mandya", "Chikkamagaluru", "Hassan", "Bijapur", "Bidar",
        "Gadag", "Chitradurga", "Raichur", "Karwar", "Hospet",
        "Kolar", "Bagalkot", "Gulbarga", "Hubli", "Vijayapura", "Hampi",
        "Sirsi", "Yadgir", "Bhadravati", "Sagar", "Channarayapatna", 
        "Chikkaballapur", "Haveri", "Ramanagara", "Humnabad", "Puttur", 
        "Karwar", "Karkala", "Alur", "Mudigere", "Kunigal", "Kadur",
    ],
    "Kerala": [
        "Thiruvananthapuram", "Kochi", "Kozhikode", "Kannur", "Kottayam",
        "Alappuzha", "Palakkad", "Thrissur", "Malappuram", "Muvattupuzha",
        "Vypin", "Varkala", "Pathanamthitta", "Ernakulam", "Punalur",
        "Kasaragod", "Payyanur", "Neyyattinkara", "Kalpetta", "Perumbavoor",
        "Thalassery", "Manjeri", "Kasargod", "Kollam", "Anchal", "Aluva",
        "Muvattupuzha", "Kanhangad", "Pattambi", "Perinthalmanna", "Sreekariyam",
        "Azhikkal", "Chalakudy", "Edappal", "Kollam", "Ponnani", "Edathala",
        "Cochin", "Irinjalakuda", "Kunnamkulam", "Changanassery", "Chirakkal",
    ],
    "Madhya Pradesh": [
        "Bhopal", "Indore", "Gwalior", "Jabalpur", "Ujjain",
        "Sagar", "Ratlam", "Rewa", "Satna", "Dewas",
        "Chhindwara", "Murwara", "Vidisha", "Shivpuri", "Neemuch",
        "Khandwa", "Balaghat", "Mandla", "Damoh", "Betul", "Hoshangabad",
        "Shahdol", "Burhanpur", "Tikamgarh", "Panna", "Seoni", "Raisen",
        "Katni", "Alirajpur", "Anuppur", "Ashoknagar", "Mandsaur", "Rajgarh",
        "Narsinghpur", "Chhatarpur", "Shivpuri", "Datia", "Chhindwara", 
        "Bhilai", "Guna", "Pichhore", "Chhattarpur", "Dhar", "Jhabua",
    ],
    "Maharashtra": [
        "Mumbai", "Pune", "Nagpur", "Thane", "Nashik", "Aurangabad", 
        "Solapur", "Kolhapur", "Amravati", "Sangli", "Latur", "Akola", 
        "Jalgaon", "Nanded", "Ratnagiri", "Chandrapur", "Dhule", 
        "Malegaon", "Ichalkaranji", "Jalna", "Ambarnath", "Badlapur", 
        "Panvel", "Ulhasnagar", "Kalyan", "Dombivli", "Vasai", 
        "Virar", "Satara", "Beed", "Alibag", "Baramati", "Shirdi", 
        "Pandharpur", "Chiplun", "Osmanabad", "Gondia", "Hingoli", 
        "Washim", "Yavatmal", "Karad", "Mahabaleshwar", "Palghar", 
        "Talegaon", "Lonavala", "Vita", "Malkapur", "Dahanu", "Manmad", 
        "Uran", "Sinnar", "Akluj", "Khamgaon", "Wai", "Pusad", 
        "Shrirampur", "Sangamner", "Pathardi", "Digras", "Barshi", 
        "Buldhana", "Kinwat", "Nandurbar", "Tumsar", "Gadchiroli", 
        "Vijayapura", "Achalpur", "Murtijapur", "Rajgurunagar", 
        "Parli", "Ambajogai", "Chandrapur", "Nagpur", "Bhandara", 
        "Wardha", "Navi Mumbai", "Aurangabad",
    ],
    "Manipur": [
        "Imphal", "Bishnupur", "Thoubal", "Churachandpur", "Kakching",
        "Jiribam", "Senapati", "Tamenglong", "Ukhrul", "Noney", 
        "Chandel", "Kangpokpi", "Moirang", "Lamlai", "Tengnoupal",
    ],
    "Meghalaya": [
        "Shillong", "Tura", "Nongpoh", "Cherrapunji", "Jowai", 
        "Mawkyrwat", "Bojan", "Nartiang", "Mairang", "Baghmara", 
        "Williamnagar", "Resubelpara", "Nongstoin", "Pynursla", 
        "Khasi Hills", "Garo Hills", "Ri Bhoi", "East Khasi Hills",
    ],
    "Mizoram": [
        "Aizawl", "Lunglei", "Serchhip", "Champhai", "Kolasib", 
        "Mamit", "Saiha", "Lawngtlai", "Hnahthial", "Kolasib", 
        "Khawzawl", "Vairengte", "Zohmun", "Tlabung", "Darlawn", 
        "Ngopa", "Thenzawl", "Bungkawn", "Siaha",
    ],
    "Nagaland": [
        "Kohima", "Dimapur", "Mokokchung", "Tuensang", "Wokha", 
        "Mon", "Phek", "Zunheboto", "Kiphire", "Longleng", 
        "Tseminyu", "Chümoukedima", "Peren", "Jalukie", 
        "Lotha", "Kohima Town", "Dimapur Town",
    ],
    "Odisha": [
        "Bhubaneswar", "Cuttack", "Rourkela", "Berhampur", "Sambalpur",
        "Puri", "Balasore", "Baripada", "Bhadrak", "Angul",
        "Dhenkanal", "Jagatsinghpur", "Jeypore", "Khordha", "Nayagarh",
        "Balangir", "Bargarh", "Kendrapara", "Koraput", "Malkangiri",
        "Sundargarh", "Nimapara", "Khariar", "Rayagada", "Pattamundai",
        "Boudh", "Deogarh", "Ganjam", "Kendujhar", "Mayurbhanj",
    ],
    "Punjab": [
        "Chandigarh", "Ludhiana", "Amritsar", "Jalandhar", "Patiala",
        "Bathinda", "Hoshiarpur", "Mohali", "Pathankot", "Ferozepur",
        "Moga", "Rupnagar", "Kapurthala", "Faridkot", "Mansa", 
        "Sri Muktsar Sahib", "Tarn Taran", "Fatehgarh Sahib", "Nawanshahr",
        "Zira", "Phagwara", "Sultanpur Lodhi", "Malerkotla", 
        "Samrala", "Ajnala", "Dera Baba Nanak", "Moga", "Rampura Phul",
    ],
    "Rajasthan": [
        "Jaipur", "Jodhpur", "Udaipur", "Kota", "Ajmer",
        "Bikaner", "Alwar", "Bharatpur", "Sikar", "Jaisalmer",
        "Pali", "Churu", "Sawai Madhopur", "Nagaur", "Barmer",
        "Tonk", "Banswara", "Dungarpur", "Jhunjhunu", "Sri Ganganagar",
        "Hanumangarh", "Jhalawar", "Karauli", "Ratangarh", "Chittorgarh",
        "Rajsamand", "Kishangarh", "Beawar", "Mandawa", "Shahpura", 
        "Merta City", "Pali", "Sirohi", "Kota", "Bhilwara", 
    ],
    "Sikkim": [
        "Gangtok", "Namchi", "Geyzing", "Mangan", "Rangpo",
        "Singtam", "Jorethang", "Rabong", "Sichey", "Tadong",
    ],
    "Tamil Nadu": [
        "Chennai", "Coimbatore", "Madurai", "Tiruchirappalli", "Salem",
        "Vellore", "Tiruppur", "Thoothukudi", "Dindigul", "Kanchipuram",
        "Tirunelveli", "Karur", "Erode", "Vikramshila", "Pollachi",
        "Puducherry", "Nagercoil", "Ramanathapuram", "Tanjore", "Kumbakonam",
        "Virudhunagar", "Dharmapuri", "Perambalur", "Sivakasi", "Namakkal",
        "Cuddalore", "Arakkonam", "Nagapattinam", "Vellore", "Chidambaram",
        "Tiruvallur", "Hosur", "Bargur", "Chengalpattu", "Ranipet", 
    ],
    "Telangana": [
        "Hyderabad", "Warangal", "Nizamabad", "Karimnagar", "Ramagundam",
        "Khammam", "Mahbubnagar", "Mancherial", "Adilabad", "Miryalaguda",
        "Siddipet", "Jagtial", "Suryapet", "Nirmal", "Peddapalli",
        "Bhadrachalam", "Bhainsa", "Kothagudem", "Jagitial", "Wanaparthy",
        "Jangaon", "Nagarkurnool", "Medak", "Vikarabad", "Peddapalli",
    ],
    "Tripura": [
        "Agartala", "Udaipur", "Kailashahar", "Dharmanagar", "Khowai",
        "Sabroom", "Belonia", "Bishalgarh", "Jirania", "Melaghar",
        "Sonamura", "Amarpur", "Madhupur", "Gakulpur", "Bagma",
    ],
    "Uttar Pradesh": [
        "Lucknow", "Kanpur", "Varanasi", "Agra", "Meerut",
        "Allahabad", "Bareilly", "Ghaziabad", "Moradabad", "Aligarh",
        "Firozabad", "Mathura", "Shahjahanpur", "Rampur", "Saharanpur",
        "Muzaffarnagar", "Bijnor", "Jaunpur", "Raebareli", "Azamgarh",
        "Prayagraj", "Etawah", "Hapur", "Gorakhpur", "Sitapur",
        "Deoria", "Budaun", "Ballia", "Hardoi", "Amroha",
        "Basti", "Kushinagar", "Chandauli", "Jalaun", "Farrukhabad",
        "Sambhal", "Unnao", "Kanshiram Nagar", "Sonbhadra", "Shahabad",
        "Tanda", "Lakhimpur Kheri", "Mirzapur", "Pilibhit", "Raebareli",
    ],
    "Uttarakhand": [
        "Dehradun", "Haridwar", "Roorkee", "Nainital", "Haldwani",
        "Rudrapur", "Almora", "Kashipur", "Pithoragarh", "Bageshwar",
        "Kotdwar", "Champawat", "Ranikhet", "Bharatpur", "Mussoorie",
        "Khatima", "Dhanaulti", "Tehri", "Lohaghat", "Kedarnath",
    ],
    "West Bengal": [
        "Kolkata", "Howrah", "Asansol", "Durgapur", "Siliguri",
        "Darjeeling", "Hooghly", "Kharagpur", "Haldia", "Jalpaiguri",
        "Malda", "Raiganj", "Cooch Behar", "Medinipur", "Balurghat",
        "Alipurduar", "Suri", "Krishnanagar", "Tamluk", "Baharampur",
        "Chinsurah", "Rampurhat", "Santipur", "Kandi", "Bongaon",
        "Basirhat", "Habra", "Barrackpore", "Nabadwip", "Katwa",
        "Bolpur", "Uluberia", "Sainthia", "Jaynagar-Majilpur", "Budge Budge",
    ],
}




def check_state_city(input_city, state, threshold: int = 1) -> bool:
    """True when ``input_city`` fuzzy-matches any city listed under ``state``."""
    for city in city_by_states_original.get(state, ()):
        if is_same(input_city, city, threshold):
            return True
    return False


def city_to_state(input_city, threshold: int = 1) -> str:
    """The state a city belongs to, or ``"Other"`` when it cannot be resolved.

    Behaviour is preserved from the reference implementation: the first state
    whose city list fuzzy-matches wins, and an unresolvable name returns the
    literal ``"Other"`` rather than raising.
    """
    if not str(input_city or "").strip():
        return "Other"
    for state in city_by_states_original:
        if check_state_city(input_city, state, threshold):
            return state
    return "Other"


def all_crawl_cities() -> list:
    """The crawl city list, flattened and de-duplicated, order preserved."""
    cities = [city for cities in city_by_states.values() for city in cities]
    return list(dict.fromkeys(cities))


# ---------------------------------------------------------------------------
# KEYWORD PLAN
# ---------------------------------------------------------------------------
# The Naukri reference project keeps a 432-entry "IT Department" list here and
# then replaces it in the main script with
#
#     job_by_department = {"IT Department": build_role_plan()}
#
# Duplicating the dead 432-entry list would be copying something the crawler
# never searches, so the plan is defined once, in target_audience_roles.py,
# and exposed here under the same name and the same shape the main script
# expects:  {department: [job title, ...]}.
try:
    from target_audience_roles import build_role_plan
    job_by_department = {"IT Department": build_role_plan()}
except ImportError:                             # pragma: no cover - broken checkout
    job_by_department = {"IT Department": []}
