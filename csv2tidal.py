#!/usr/bin/env python3

import sys
import argparse
import csv
import tidalapi
from auth import open_tidal_session

parser = argparse.ArgumentParser(description='Import CSV albums into a Tidal playlist.')
parser.add_argument('file', help='CSV file with columns: artist,title')
parser.add_argument('--playlist', default='csv2tidal import', help='Tidal playlist name (default: csv2tidal import)')
args = parser.parse_args()

tidal_session = open_tidal_session()
if not tidal_session.check_login():
    sys.exit("Could not connect to Tidal")

# Get or create playlist
playlist = None
for pl in tidal_session.user.playlists():
    if pl.name == args.playlist:
        playlist = pl
        break
if playlist is None:
    playlist = tidal_session.user.create_playlist(args.playlist, f'Imported via csv2tidal')

print(f"Importing into playlist: {playlist.name}")

track_ids = []
with open(args.file, encoding='utf8') as csvfile:
    for artist, album in csv.reader(csvfile):
        print(f'Processing {artist} - {album}')
        results = tidal_session.search(f'{artist} {album}', models=[tidalapi.Track], limit=3)
        hits = results.get('tracks', [])
        if not hits:
            print(f'WARNING: No tracks found for {artist} - {album}, skipping\n')
            continue
        track_ids.append(str(hits[0].id))

if track_ids:
    playlist.add(track_ids)
    print(f'\nDone. Added {len(track_ids)} tracks to "{playlist.name}".')
else:
    print('\nNo tracks found to add.')
