#!/usr/bin/env python3

import MySQLdb
import requests
from multiprocessing import Pool
import json, re
import functions

cfg = json.load(open('config.json'))

def scrape_posts(account):
	db = MySQLdb.connect(
		host = cfg['db_host'],
		user=cfg['db_user'],
		passwd=cfg['db_pass'],
		db=cfg['db_name'],
		use_unicode=True,
		charset="utf8mb4"
	)
	handle = account[0]
	outbox = account[1]
	print("Scraping {}".format(handle))
	c = db.cursor()
	last_post = 0
	c.execute("SELECT COUNT(*) FROM `posts` WHERE `fedi_id` = %s", (handle,))
	count = c.fetchone()
	if count is not None and int(count[0]) > 0:
		# we've downloaded this user's posts before
		# find out the most recently downloaded post of theirs
		c.execute("SELECT `post_id` FROM `posts` WHERE `fedi_id` = %s ORDER BY `id` DESC LIMIT 1", (handle,))
		last_post = c.fetchone()[0]

	r = requests.get(outbox)
	j = r.json()
	# check for pleroma
	pleroma = 'next' not in j
	if pleroma:
		j = j['first']
	else:
		uri = "{}&min_id={}".format(outbox, last_post)
		r = requests.get(uri)
		j = r.json()

	# here we go!
	# warning: scraping posts from outbox.json is messy stuff
	done = False
	while not done and len(j['orderedItems']) > 0:
		for oi in j['orderedItems']:
			if oi['type'] == "Create":
				# this is a status/post/toot/florp/whatever
				# first, check to see if we already have this in the database
				post_id = re.search(r"([^\/]+)/?$", oi['object']['id']).group(1) # extract 123 from https://example.com/posts/123/
				c.execute("SELECT COUNT(*) FROM `posts` WHERE `fedi_id` = %s AND `post_id` = %s", (handle, post_id))
				count = c.fetchone()
				if count is not None and int(count[0]) > 0:
					# this post is already in the DB.
					# we'll set done to true because we've caught up to where we were last time.
					done = True
					# we'll still iterate over the rest of the posts, though, in case there are still some new ones on this page.
					continue

				content = oi['object']['content']
				# remove HTML tags and such from post
				content = functions.extract_post(content)

				if len(content) > 65535:
					# post is too long to go into the DB
					continue

				try:
					c.execute("INSERT INTO `posts` (`fedi_id`, `post_id`, `content`, `cw`) VALUES (%s, %s, %s, %s)", (
						handle,
						post_id,
						content,
						1 if (oi['object']['summary'] != None and oi['object']['summary'] != "") else 0
					))
				except:
					#TODO: error handling
					raise

		if not done:
			if pleroma:
				r = requests.get(j['next'], timeout = 10)
			else:
				r = requests.get(j['prev'], timeout = 10)

			if r.status_code == 429:
				# we are now being ratelimited, move on to the next user
				print("Hit rate limit while scraping {}".format(handle))
				done = True
			else:
				j = r.json()

			db.commit()

		db.commit()
		print("Finished scraping {}".format(handle))

print("Establishing DB connection")
db = MySQLdb.connect(
	host = cfg['db_host'],
	user=cfg['db_user'],
	passwd=cfg['db_pass'],
	db=cfg['db_name'],
	use_unicode=True,
	charset="utf8mb4"
)

cursor = db.cursor()

print("Downloading posts")
cursor.execute("SELECT `handle`, `outbox` FROM `fedi_accounts` ORDER BY RAND()")
accounts = cursor.fetchall()
cursor.close()
db.close()
with Pool(cfg['service_threads']) as p:
	p.map(scrape_posts, accounts)

print("Done!")
