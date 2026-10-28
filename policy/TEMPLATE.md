# Occupancy Monitoring Policy

<!--
Fill in every section, replacing all <placeholders>. `wifi-utility policy publish` rejects the
file if a section is missing, empty, or still contains a <placeholder>, TODO or TBD.
Section headings must stay exactly as written. This template is not legal advice: have it
reviewed for the jurisdictions where the zone is located.
-->

## Employer and contact
<Employer legal name. Name, email and phone of the person who answers questions about this policy.>

## Zones and sensor location
<Which room/zone is monitored (zone id as in config, e.g. meeting-room-1) and where the Mac acting as sensor is placed.>

## What is measured
Motion-based occupancy of the zone, in 15-minute periods: for each 15-minute period the system
stores only whether the zone was occupied or vacant. This is inferred from changes in the WiFi
signal between the sensor Mac and the office router.

## What is not measured
The system does not record identity, individual presence or movements, how many people are in
the zone, device data, network traffic, audio or video. It cannot tell who is in the room or
whether a still person is present.

## Purpose
<Why the data is collected, e.g. "meeting-room utilisation planning" or "HVAC scheduling". Add "after-hours security" here only if after-hours alerts are switched on.>

## Hours of operation
<One schedule per line, e.g.:
- mon-fri 08:00-18:00>

## Retention period
<Number of days occupancy data is kept, at most 90, e.g. "30 days".>

## Who can access the data
<Roles that can see the admin dashboard and exports.>

## How to see the data and raise concerns
Everyone can see the same occupancy data the employer sees on the public policy page
(<http://sensor-address:8765/policy>). <How to raise concerns or objections, and who responds.>

## Applicable law and consultation
<Applicable laws checked, and the status of any consultation with a works council, union or staff representatives.>

## Effective date
<YYYY-MM-DD — at least the configured notice period after publishing.>

## Acknowledgement records names
<"no" (default: acknowledgements are anonymous, only count and time are stored) or "yes".>
