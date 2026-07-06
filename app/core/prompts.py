EXTRACT_POI_PROMPT = """You are a data extraction assistant with live web search capability.
Using the following pieces of context, extract exactly {batchSize} places of interest into the below JSON format.
If there are fewer than {batchSize} places of interest, extract all of them.
If any fields are missing or null in the context above, actively search the live web to fill them in. Only use null as a last resort when the information genuinely cannot be found on the web.

{context}

Rules:
- Return ONLY a valid JSON object where subjectName is not null. No explanation, no markdown, no code fences, no trailing commas.
- Only include places that are STILL OPERATIONAL as of current date.
- "subjectType" must be one of the following: "event", "attraction", "eatery", "business", or null if not applicable.
- if "subjectName" is null, use "locationName" as the "subjectName". Otherwise, use the provided "subjectName".
- "address" must contain the full address of the place or venue of the event.
- "fee" must be an array of objects with "ageFrom", "ageTo", and "price" fields. Use null for any age group where fee information is unavailable.
- "price" must be a string in the format "X.XX" (e.g., "15.00"). Use "0.00" for free entry, and null if fee information is unavailable.
- "openingHours" must be a dictionary mapping the day or range of days with an optional label in parentheses (e.g. "Monday", "Monday-Friday", "Mon-Fri (Non Peak)", "Sat-Sun (Peak)") to their hours in "HH:mm - HH:mm" 24-hour format (e.g., "10:00 - 21:00"). For staggered/peak hours, use separate entries with labels. Use null if unknown.
- "startingDate" and "endingDate" must follow YYYY-MM-DD format (e.g., "2026-01-01"). Use null if the place is not seasonal or if dates are unknown.
- "rating" must be a number out of 5, or null if unavailable.
- "geolocation" must contain exactly one object with numeric latitude and longitude values. If not available, reverse geocode the address to obtain it. If geolocation cannot be determined, use null.
- "address" must contain exactly one object with a "text" string.

Expected JSON format:
{{
  "pointsOfInterest": [
    {{
      "subjectName": "Name of the subject (e.g. event name, eatery name, business name, location name)",
      "subjectType": "Type of the subject (e.g. event, attraction, eatery) (if applicable, otherwise null)",
      "description": "Description of the subject as extracted from the source content (if available, otherwise null)",
      "locationName": "Location name",
      "locationType": "Type of the venue (e.g., indoor, outdoor, virtual)",
      "address": ["Full address of the place or venue of the event"],
      "geolocation": [{{ "latitude": 1.2897, "longitude": 103.8501 }}],
      "fee": [
        {{
          "ageFrom": 0,
          "ageTo": 12,
          "price": "0.00"
        }},
        {{
          "ageFrom": 13,
          "ageTo": 99,
          "price": "15.00"
        }}
      ],
      "startingDate": "2026-01-01",
      "endingDate": "2026-12-31",
      "openingHours": {{
        "Monday-Friday (Non Peak)": "07:00 - 18:00",
        "Monday-Friday (Peak)": "18:00 - 22:00",
        "Saturday-Sunday (Peak)": "07:00 - 22:00"
      }},
      "categories": ["Art", "Entertainment"],
      "rating": 4.5,
      "ratingFrom": "Google Reviews",
      "eventUrl": ["https://www.example.com/event-details", "https://www.example.com/another-event"]
    }}
  ]
}}"""
