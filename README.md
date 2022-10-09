# PH OpenStreetMap Edits Project
*A simple program that parses metadata of OpenStreetMap changes in the Philippines. Based on [ChangesetMD](https://github.com/mvexel/ChangesetMD).*

## How it works
The program goes through OpenStreetMap [changeset](https://wiki.openstreetmap.org/wiki/Changeset) dump files or replication files and looks for changesets in the Philippines. It determines whether or not the changes made are located in the Philippines by taking the centroid of the changeset's [bounding box](https://wiki.openstreetmap.org/wiki/Bounding_Box) and comparing these coordinates to a GeoJSON file of the country's national borders. After this, it identifies what city, province, and region contains the changeset's centroid coordinates using GeoJSON files of each of the country's administrative levels. The data is then stored in a PostgreSQL database.

### Tableau Visualization
A rudimentary overview of the data as of February 2022.

## How to use it
*This program only parses and loads OpenStreetMap metadata from the Philippines. For most purposes, [ChangesetMD](https://github.com/mvexel/ChangesetMD) should be more useful*
### Setup
1. If using a virtualenv, install dependencies with `pip install -r requirements.txt`.
2. Set up a PostgreSQL database. You can also use the docker compose configuration file by running `docker-compose up -d` within this directory.
### Execution
1. If running for the first time, you will need to pass arguments to create tables in the database and for the changeset dump file to be parsed:
```
./changesetmd.py -c -f {changeset dump .bz2 file path} -d {db name} -H {db host} -P {db port} -u {db username} -p {db password}
```
You can download the latest dump file here: http://planet.osm.org/replication/changesets/
### Replication
1. Run the following command regularly, in a cron job if you like:
```
./changesetmd.py -r -d {db name} -H {db host} -P {db port} -u {db username} -p {db password}
```

## Notes
- As of now, the geography reference tables are populated using data extracted in early 2022. While changes based on the September 2022 plebiscites that split Maguindanao into two and granted cityhood to Calaca will appear in the database, the metadata assigned to changesets in those areas from then on will be inaccurate. To fix this, I can add a function that updates the reference tables and GeoJSON files alongside replication, while keeping historical values.
- A changeset that makes no edits to the Philippines may not be filtered out if the centroid of its bounding box falls within the Philippine borders.

## Attribution
Metadata in visualization from [OpenStreetMap](https://www.openstreetmap.org/copyright).
