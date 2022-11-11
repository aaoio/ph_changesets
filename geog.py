import json
import pandas as pd
import re
import numpy as np
from shapely.geometry import shape, Point
from sqlalchemy import create_engine
import psycopg2.extras as extras

def calculate_centroid(min_lon, max_lon, min_lat, max_lat):
    """
    Return centroid point of changeset bounding box.
    """
    centroid_lon = np.mean((float(min_lon), float(max_lon)))
    centroid_lat = np.mean((float(min_lat), float(max_lat)))
    centroid = (centroid_lon, centroid_lat)
    return(centroid)

def check_if_in_philippines(ph_polygon, point):
    """Return boolean of whether point is in the Philippines"""
    return ph_polygon.contains(point)

def locate_in_philippines(geojson_file, point):
    """
    Return relation ID of feature within a geojson file
    containing a given point.
    """
    for x in geojson_file['features']:
        polygon = shape(x['geometry'])
        if 'name' in x['properties'] and polygon.contains(point):
            return(x['properties']['@id'])

def geog_reference_tables(cursor):
    with open('GeoJSON/l6_cities_municipalities.geojson') as f:
        ph_cm = json.load(f)
    with open('GeoJSON/l4_provinces.geojson') as f:
        ph_p = json.load(f)
    with open('GeoJSON/l3_regions.geojson') as f:
        ph_r = json.load(f)
    provinces = pd.read_csv('geog_tables/provinces.csv', index_col=0)
    
    ### CITIES
    
    # Sort features by what identifying properties they have
    with_wikidata = [x['properties']['wikidata']
                    for x in ph_cm['features'] 
                    if 'name' in x['properties']
                    and 'wikidata' in x['properties']]
    without_wikidata = [x
                        for x in ph_cm['features'] 
                        if 'name' in x['properties']
                        and 'wikidata' not in x['properties']]
    isin_pattern = re.compile(r'is_in:?\w*')
    with_isin = [x['properties']
                for x in ph_cm['features']
                if 'name' in x['properties']
                for y in x['properties']
                if re.match(isin_pattern, y)]
    without_wikidata_with_isin = [x
                                for x in without_wikidata
                                for y in x['properties']
                                if re.match(isin_pattern, y)]
    without_wikidata_without_isin = [x
                                    for x in without_wikidata
                                    if x not in without_wikidata_with_isin]
    
    # Create wikidata dataframe
    wikidata = pd.read_csv('geog_tables/wikidataqueryoutput.csv')
    wikidata['item'] = wikidata['item'].str[31:]
    
    # Create wikidata-relation dataframe
    wd_rel_pairs = [[x['properties']['wikidata'], x['properties']['@id']]
                    for x in ph_cm['features']
                    if ('name' in x['properties'])
                    and ('wikidata' in x['properties'])]
    wd_rel_df = pd.DataFrame(wd_rel_pairs, columns=['wikidata', 'relation'])
    
    # Create geography reference dataframe
    cm_df = pd.merge(wd_rel_df, wikidata,
                                left_on='wikidata',
                                right_on='item',
                                how='inner')
    cm_df = cm_df.drop('item', axis=1)
    cm_df.columns = [
        'wikidata_entry',
        'relation_id',
        'city_or_mun',
        'province_or_region',
        'type',
        'income_class',
        'population'
        ]
    
    # Create relation-"is_in" property database for features
    # for features without wikidata property
    isin_rel = [[x['properties']['name'],
                x['properties'][isin],
                x['properties']['@id']]
                for x in without_wikidata_with_isin
                for isin in x['properties']
                if re.match(isin_pattern, isin)]
    isin_rel_df = pd.DataFrame(
        isin_rel,
        columns=['city_or_mun',
            'province_or_region',
            'relation']
        )
    isin_rel_wd = pd.merge(
        wikidata,
        isin_rel_df,
        left_on=[
            wikidata['itemLabel'].str.lower(),
            wikidata['withinLabel'].str.lower()
            ],
        right_on=[
            isin_rel_df['city_or_mun'].str.lower(),
            isin_rel_df['province_or_region'].str.lower()
            ],
        how='inner'
        )
    isin_rel_wd = isin_rel_wd[
        ['item', 'relation', 'city_or_mun',
        'province_or_region', 'instanceofLabel',
        'incomeclassLabel', 'population']
        ]
    isin_rel_wd.columns = cm_df.columns
    cm_df = pd.concat([cm_df, isin_rel_wd],
                                ignore_index=True)
                                
    # Remove elements from wikidata df
    # that are already in geography reference df
    no_match_mask = wikidata['item'].isin(cm_df['wikidata_entry'])
    wd_no_match = wikidata[~no_match_mask]
    wd_no_match_counts = wd_no_match['itemLabel'].value_counts()
    
    # Match features without wikidata and "is in" properties 
    # with wikidata entries if the wikidata entry has no other namesake
    without_wd_without_isin_wd = [[wd_no_match[wd_no_match['itemLabel']==
        x['properties']['name']]['item'].values[0],
        x['properties']['@id'], x['properties']['name'],
        wd_no_match[wd_no_match['itemLabel']==
        x['properties']['name']]['withinLabel'].values[0],
        wd_no_match[wd_no_match['itemLabel']==
        x['properties']['name']]['instanceofLabel'].values[0],
        wd_no_match[wd_no_match['itemLabel']==
        x['properties']['name']]['incomeclassLabel'].values[0],
        wd_no_match[wd_no_match['itemLabel']==
        x['properties']['name']]['population'].values[0]]
        for x in without_wikidata_without_isin
        if x['properties']['name'] in wd_no_match_counts.index
        and wd_no_match_counts[x['properties']['name']]==1
    ]
    
    wo_wd_wo_isin_wd_df = pd.DataFrame(without_wd_without_isin_wd,
                                        columns=cm_df.columns)
    
    cm_df = pd.concat([cm_df, wo_wd_wo_isin_wd_df],
                                axis=0,
                                ignore_index=True
                                )
    cm_df['wikidata_entry'] = cm_df['wikidata_entry'].apply(lambda x: x[1:])
    cm_df['relation_id'] = cm_df['relation_id'].apply(lambda x: x[9:])
    cm_df.name = 'city_municipality_reference'
                                
    ### PROVINCES
    provinces_list = [
        [x['properties']['wikidata'],
        x['properties']['@id'],
        x['properties']['name']]
        for x in ph_p['features'] if 'name' in x['properties']]
    p_df_cols=['wikidata_entry', 'relation_id', 'province']
    p_df = pd.DataFrame(provinces_list, columns=p_df_cols)
    
    p_df['wikidata_entry'] = p_df['wikidata_entry'].apply(lambda x: x[1:])
    p_df['relation_id'] = p_df['relation_id'].apply(lambda x: x[9:])
    p_df.name = 'province_reference'
    
    ### REGIONS
    regions_list = [
        [x['properties']['wikidata'],
        x['properties']['@id'],
        x['properties']['name']]
        for x in ph_r['features'] if 'name' in x['properties']]
    r_df_cols=['wikidata_entry', 'relation_id', 'region']
    r_df = pd.DataFrame(regions_list, columns=r_df_cols)
    
    r_df['wikidata_entry'] = r_df['wikidata_entry'].apply(lambda x: x[1:])
    r_df['relation_id'] = r_df['relation_id'].apply(lambda x: x[9:])
    r_df.name = 'region_reference'
    
    ### Add to database
    df_list = [cm_df, p_df, r_df]      
    for df in df_list:
        tuples = [tuple(x) for x in df.to_numpy()]
        query = f"""
            INSERT INTO {df.name} ({','.join(list(df.columns))}) VALUES %s          
            """
        extras.execute_values(cursor, query, tuples)
