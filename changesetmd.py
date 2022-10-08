#!/usr/bin/env python
'''
ChangesetMD is a simple XML parser to read the weekly changeset metadata dumps
from OpenStreetmap into a postgres database for querying.

@author: Toby Murray
'''

from __future__ import print_function
import sys
import argparse
import gzip
from datetime import datetime
from datetime import timedelta
import psycopg2
import psycopg2.extras
import queries
import requests
import yaml
from lxml import etree
import geog
from shapely.geometry import shape, Point
import json
import bz2
from bs4 import BeautifulSoup
import lxml
import re
from datetime import datetime, timedelta

try:
    from bz2file import BZ2File
    bz2Support = True
except ImportError:
    bz2Support = False

BASE_REPL_URL = "https://planet.openstreetmap.org/replication/changesets/"

class ChangesetMD():
    def __init__(self, createGeometry):
        self.createGeometry = createGeometry

    def truncateTables(self, connection):
        print('truncating tables')
        cursor = connection.cursor()
        cursor.execute("TRUNCATE TABLE osm_changeset_comment CASCADE;")
        cursor.execute("TRUNCATE TABLE osm_changeset CASCADE;")
        cursor.execute(queries.dropIndexes)
        cursor.execute("UPDATE osm_changeset_state set last_sequence = -1, last_timestamp = null, update_in_progress = 0")
        connection.commit()

    def createTables(self, connection):
        print('creating tables')
        cursor = connection.cursor()
        cursor.execute(queries.createChangesetTable)
        geog.geog_reference_tables(cursor)
        cursor.execute(queries.initStateTable)
        if self.createGeometry:
            cursor.execute(queries.createGeometryColumn)
        connection.commit()

    def insertNewBatch(self, connection, data_arr):
        cursor = connection.cursor()
        if self.createGeometry:
            sql = '''INSERT into osm_changeset
                    (id, user_id, created_at, min_lat, max_lat, min_lon, max_lon, centroid_lon, centroid_lat, closed_at, open, num_changes, user_name, city_id, province_id, region_id, tags, geom)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,ST_SetSRID(ST_MakeEnvelope(%s,%s,%s,%s), 4326))'''
            psycopg2.extras.execute_batch(cursor, sql, data_arr)
            cursor.close()
        else:
            sql = '''INSERT into osm_changeset
                    (id, user_id, created_at, min_lat, max_lat, min_lon, max_lon, centroid_lon, centroid_lat, closed_at, open, num_changes, user_name, city_id, province_id, region_id, tags)
                    values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)'''
            psycopg2.extras.execute_batch(cursor, sql, data_arr)
            cursor.close()

    def insertNewBatchComment(self, connection, comment_arr):
        cursor=connection.cursor()
        sql = '''INSERT into osm_changeset_comment
                    (comment_changeset_id, comment_user_id, comment_user_name, comment_date, comment_text)
                    values (%s,%s,%s,%s,%s)'''
        psycopg2.extras.execute_batch(cursor, sql, comment_arr)
        cursor.close()

    def deleteExisting(self, connection, id):
        cursor = connection.cursor()
        cursor.execute('''DELETE FROM osm_changeset_comment
                          WHERE comment_changeset_id = %s''', (id,))
        cursor.execute('''DELETE FROM osm_changeset
                          WHERE id = %s''', (id,))

    def parseFile(self, connection, changesetFile, doReplication):
        parsedCount = 0
        PH_parsedCount = 0
        startTime = datetime.now()
        cursor = connection.cursor()
        context = etree.iterparse(changesetFile)
        action, root = next(context)
        changesets = []
        comments = []
        attribs_used = [
            'id', 'uid', 'open', 'created_at', 'closed_at', 'min_lon', 'max_lon',
            'min_lat', 'max_lat'
            ]
        
        with open('GeoJSON/l2_national.geojson') as f:
            ph = json.load(f)
        ph_polygon = shape(ph['features'][0]['geometry'])

        with open('GeoJSON/l3_regions.geojson') as f:
            ph_r = json.load(f)

        with open('GeoJSON/l4_provinces.geojson') as f:
            ph_p = json.load(f)

        with open('GeoJSON/l6_cities_municipalities.geojson') as f:
            ph_cm = json.load(f)
        
        for action, elem in context:
            if any(x not in elem.attrib for x in attribs_used):
                continue
            if (elem.tag != 'changeset') or (elem.attrib['open'] != 'false'):
                continue

            parsedCount += 1
            
            centroid_coordinates = geog.calculate_centroid(
                                    elem.attrib['min_lon'],
                                    elem.attrib['max_lon'],
                                    elem.attrib['min_lat'],
                                    elem.attrib['max_lat']
                                    )
            centroid = Point(centroid_coordinates)
            if geog.check_if_in_philippines(ph_polygon, centroid) == False:
                continue
            
            PH_parsedCount += 1
            
            city = geog.locate_in_philippines(ph_cm, centroid)
            province = geog.locate_in_philippines(ph_p, centroid)
            region = geog.locate_in_philippines(ph_r, centroid)
            
            if city:
                city_id = city[9:]
            else:
                city_id = city
            
            if province:
                province_id = province[9:]
            else:
                province_id = province
                
            if region:
                region_id = region[9:]
            else:
                region_is = region

            tags = {}
            for tag in elem.iterchildren(tag='tag'):
                tags[tag.attrib['k']] = tag.attrib['v']
                
            for discussion in elem.iterchildren(tag='discussion'):
                for commentElement in discussion.iterchildren(tag='comment'):
                    for text in commentElement.iterchildren(tag='text'):
                       text = text.text
                    comment = (elem.attrib['id'], commentElement.attrib.get('uid'),  commentElement.attrib.get('user'), commentElement.attrib.get('date'), text)
                    comments.append(comment)

            if(doReplication):
                self.deleteExisting(connection, elem.attrib['id'])

            if self.createGeometry:
                changesets.append((elem.attrib['id'], elem.attrib.get('uid', None),   elem.attrib['created_at'], elem.attrib.get('min_lat', None),
                                elem.attrib.get('max_lat', None), elem.attrib.get('min_lon', None),  elem.attrib.get('max_lon', None), centroid_coordinates[0], centroid_coordinates[1], elem.attrib.get('closed_at', None),
                                     elem.attrib.get('open', None), elem.attrib.get('num_changes', None), elem.attrib.get('user', None), city_id, province_id, region_id, tags,elem.attrib.get('min_lon', None), elem.attrib.get('min_lat', None),
                                    elem.attrib.get('max_lon', None), elem.attrib.get('max_lat', None)))
            else:
                changesets.append((elem.attrib['id'], elem.attrib.get('uid', None),   elem.attrib['created_at'], elem.attrib.get('min_lat', None),
                                elem.attrib.get('max_lat', None), elem.attrib.get('min_lon', None),  elem.attrib.get('max_lon', None), centroid_coordinates[0], centroid_coordinates[1], elem.attrib.get('closed_at', None),
                                     elem.attrib.get('open', None), elem.attrib.get('num_changes', None), elem.attrib.get('user', None), city_id, province_id, region_id, tags))

            if((PH_parsedCount % 10000) == 0):
                self.insertNewBatch(connection, changesets)
                self.insertNewBatchComment(connection, comments )
                changesets = []
                comments = []
                print(f"total PH changesets parsed: {PH_parsedCount}")
                print("total parsed {}".format(('{:,}'.format(parsedCount))))
                print("cumulative rate: {}/sec".format('{:,.0f}'.format(parsedCount/timedelta.total_seconds(datetime.now() - startTime))))

            #clear everything we don't need from memory to avoid leaking
            elem.clear()
            while elem.getprevious() is not None:
                del elem.getparent()[0]
        # Update whatever is left, then commit
        self.insertNewBatch(connection, changesets)
        self.insertNewBatchComment(connection, comments)
        connection.commit()
        print("parsing complete")
        print("parsed {:,}".format(parsedCount))

    def fetchReplicationFile(self, sequenceNumber):
        sequenceNumber = str(sequenceNumber).zfill(9)
        topdir = str(sequenceNumber)[:3]
        subdir = str(sequenceNumber)[3:6]
        fileNumber = str(sequenceNumber)[-3:]
        fileUrl = BASE_REPL_URL + topdir + '/' + subdir + '/' + fileNumber + '.osm.gz'
        print("opening replication file at " + fileUrl)
        replicationFile = requests.get(fileUrl, stream=True)
        replicationData = replicationFile.raw
        f = gzip.GzipFile(fileobj=replicationData)
        return f
    
    def set_initial_sequence(self, connection, changesetFile):
        """
        Set osm_changeset_state to the last sequence according
        to the changesetFile.
        """
        ts_pattern = re.compile(
            r'[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}'
        )
        ts_format = '%Y-%m-%d %H:%M'
        last_ts_format = '%Y-%m-%dT%H:%M:%SZ'
        name_pattern = re.compile(r'[0-9]{3}')
        
        changesets = bz2.BZ2File(changesetFile)
        peek_file = changesets.peek()
        peek_file_bs4 = BeautifulSoup(peek_file, 'xml')
        tag = peek_file_bs4.osm
        last_time = tag['timestamp']
        last_ts = datetime.strptime(last_time, last_ts_format)
        
        repl_url = BASE_REPL_URL
        sequence = ''
        subdirs = 0
        
        while subdirs!=3:
            result = requests.get(repl_url)
            repl_site = BeautifulSoup(result.text, 'html.parser')
            
            a_tags = repl_site.find_all(text=name_pattern)
            
            ts_dict = {}
            for tag in a_tags:
                ts_text = tag.parent.next_sibling.strip()
                if ts_text:
                    ts = ts_pattern.match(ts_text).group(0)
                    ts_dict[datetime.strptime(ts, ts_format)] = tag
            
            ts_deltas_dict = {
                ts_dict[key] : key - last_ts
                for key in ts_dict.keys()
                if key - last_ts >= timedelta(0)
            }
            
            nextdir = min(ts_deltas_dict.keys())
            sequence += nextdir[:3]
            repl_url = repl_url + nextdir
            
            subdirs += 1
        
        last_sequence = sequence.lstrip('0')
        cursor = connection.cursor()
        query = "update osm_changeset_state set last_sequence = %s;"
        cursor.execute(query, (last_sequence,))
        connection.commit()


    def doReplication(self, connection):
        cursor = connection.cursor(cursor_factory=psycopg2.extras.DictCursor)
        try:
            cursor.execute('LOCK TABLE osm_changeset_state IN ACCESS EXCLUSIVE MODE NOWAIT')
        except psycopg2.OperationalError as e:
            print("error getting lock on state table. Another process might be running")
            return 1
        cursor.execute('select * from osm_changeset_state')
        dbStatus = cursor.fetchone()
        lastDbSequence = dbStatus['last_sequence']
        timestamp = None
        lastServerTimestamp = None
        newTimestamp = None
        if(dbStatus['last_timestamp'] is not None):
            timestamp = dbStatus['last_timestamp']
        print("latest timestamp in database: " + str(timestamp))
        if(dbStatus['update_in_progress'] == 1):
            print("concurrent update in progress. Bailing out!")
            return 1
        if(lastDbSequence == -1):
            print("replication state not initialized. You must set the sequence number first.")
            return 1
        cursor.execute('update osm_changeset_state set update_in_progress = 1')
        connection.commit()
        print("latest sequence from the database: " + str(lastDbSequence))

        #No matter what happens after this point, execution needs to reach the update statement
        #at the end of this method to unlock the database or an error will forever leave it locked
        returnStatus = 0
        try:
            serverState = yaml.load(requests.get(BASE_REPL_URL + "state.yaml").text)
            lastServerSequence = serverState['sequence']
            print("got sequence")
            lastServerTimestamp = serverState['last_run']
            print("last timestamp on server: " + str(lastServerTimestamp))
        except Exception as e:
            print("error retrieving server state file. Bailing on replication")
            print(e)
            returnStatus = 2
        else:
            try:
                print("latest sequence on OSM server: " + str(lastServerSequence))
                if(lastServerSequence > lastDbSequence):
                    print("server has new sequence. commencing replication")
                    currentSequence = lastDbSequence + 1
                    while(currentSequence <= lastServerSequence):
                        self.parseFile(connection, self.fetchReplicationFile(currentSequence), True)
                        cursor.execute('update osm_changeset_state set last_sequence = %s', (currentSequence,))
                        connection.commit()
                        currentSequence += 1
                    timestamp = lastServerTimestamp
                print("finished with replication. Clearing status record")
            except Exception as e:
                print("error during replication")
                print(e)
                returnStatus = 2
        cursor.execute('update osm_changeset_state set update_in_progress = 0, last_timestamp = %s', (timestamp,))
        connection.commit()
        return returnStatus

if __name__ == '__main__':
    beginTime = datetime.now()
    endTime = None
    timeCost = None

    argParser = argparse.ArgumentParser(description="Parse OSM Changeset metadata into a database")
    argParser.add_argument('-t', '--trunc', action='store_true', default=False, dest='truncateTables', help='Truncate existing tables (also drops indexes)')
    argParser.add_argument('-c', '--create', action='store_true', default=False, dest='createTables', help='Create tables')
    argParser.add_argument('-H', '--host', action='store', dest='dbHost', help='Database hostname')
    argParser.add_argument('-P', '--port', action='store', dest='dbPort', default=None, help='Database port')
    argParser.add_argument('-u', '--user', action='store', dest='dbUser', default=None, help='Database username')
    argParser.add_argument('-p', '--password', action='store', dest='dbPass', default=None, help='Database password')
    argParser.add_argument('-d', '--database', action='store', dest='dbName', help='Target database', required=True)
    argParser.add_argument('-f', '--file', action='store', dest='fileName', help='OSM changeset file to parse')
    argParser.add_argument('-r', '--replicate', action='store_true', dest='doReplication', default=False, help='Apply a replication file to an existing database')
    argParser.add_argument('-g', '--geometry', action='store_true', dest='createGeometry', default=False, help='Build geometry of changesets (requires postgis)')
    argParser.add_argument('-s', '--setinitial', action='store', dest='sequenceFile', default=None, help='OSM changeset file to find last sequence of')

    args = argParser.parse_args()

    conn = psycopg2.connect(database=args.dbName, user=args.dbUser, password=args.dbPass, host=args.dbHost, port=args.dbPort)


    md = ChangesetMD(args.createGeometry)
    if args.truncateTables:
        md.truncateTables(conn)

    if args.createTables:
        md.createTables(conn)

    psycopg2.extras.register_hstore(conn)
    
    if not (args.sequenceFile is None):
        md.set_initial_sequence(conn, args.sequenceFile)

    if(args.doReplication):
        returnStatus = md.doReplication(conn)
        sys.exit(returnStatus)

    if not (args.fileName is None):
        if args.createGeometry:
            print('parsing changeset file with geometries')
        else:
            print('parsing changeset file')
        changesetFile = None
        if(args.doReplication):
            changesetFile = gzip.open(args.fileName, 'rb')
        else:
            if(args.fileName[-4:] == '.bz2'):
                if(bz2Support):
                    changesetFile = BZ2File(args.fileName)
                else:
                    print('ERROR: bzip2 support not available. Unzip file first or install bz2file')
                    sys.exit(1)
            else:
                changesetFile = open(args.fileName, 'rb')

        if(changesetFile != None):
            md.parseFile(conn, changesetFile, args.doReplication)
        else:
            print('ERROR: no changeset file opened. Something went wrong in processing args')
            sys.exist(1)

        if(not args.doReplication):
            cursor = conn.cursor()
            print('creating constraints')
            cursor.execute(queries.createConstraints)
            print('creating indexes')
            cursor.execute(queries.createIndexes)
            print('setting initial sequence')
            md.set_initial_sequence(conn, args.fileName)
            if args.createGeometry:
                cursor.execute(queries.createGeomIndex)
            conn.commit()

        conn.close()

    endTime = datetime.now()
    timeCost = endTime - beginTime

    print('Processing time cost is ', timeCost)

    print('All done. Enjoy your (meta)data!')
