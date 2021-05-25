#!/usr/bin/env python

"""Creating chess puzzles for lichess.org"""

import argparse
import io
import logging
import sys

import chess.pgn
import chess.uci

import pymongo

from modules.api.api import post_puzzle
from modules.bcolors.bcolors import bcolors
from modules.fishnet.fishnet import stockfish_command
from modules.investigate.investigate import investigate
from modules.puzzle.puzzle import puzzle


def str2bool(v):
    if isinstance(v, bool):
        return v
    if v.lower() in ('yes', 'true', 't', 'y', '1'):
        return True
    elif v.lower() in ('no', 'false', 'f', 'n', '0'):
        return False
    else:
        raise argparse.ArgumentTypeError('Boolean value expected.')


def prepare_settings():
    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument("--max", metavar="MAX", nargs="?", type=int, default=20,
                        help="number of games to retrieve")
    parser.add_argument("--user", metavar="USER", nargs="?", type=str,
                        help="user to retrieve games")
    parser.add_argument("--threads", metavar="THREADS", nargs="?", type=int, default=4,
                        help="number of engine threads")
    parser.add_argument("--memory", metavar="MEMORY", nargs="?", type=int, default=2048,
                        help="memory in MB to use for engine hashtables")
    parser.add_argument("--depth", metavar="DEPTH", nargs="?", type=int, default=8,
                        help="depth for stockfish analysis")
    parser.add_argument("--quiet", dest="loglevel",
                        default=logging.DEBUG, action="store_const", const=logging.INFO,
                        help="substantially reduce the number of logged messages")
    parser.add_argument("--games", metavar="GAMES", default="games.pgn",
                        help="A specific pgn with games")
    parser.add_argument("--strict", metavar="STRICT", default=True,
                        help="If False then it will be generate more tactics but maybe a little ambiguous")
    parser.add_argument("--includeBlunder", metavar="INCLUDE_BLUNDER", default=True,
                        type=str2bool, const=True, dest="include_blunder", nargs="?",
                        help="If False then generated puzzles won't include initial blunder move")

    return parser.parse_args()


settings = prepare_settings()
try:
    # Optionally fix colors on Windows and in journals if the colorama module
    # is available.
    import colorama

    wrapper = colorama.AnsiToWin32(sys.stdout)
    if wrapper.should_wrap():
        sys.stdout = wrapper.stream
except ImportError:
    pass

logging.basicConfig(format="%(message)s", level=settings.loglevel, stream=sys.stdout)
logging.getLogger("requests.packages.urllib3").setLevel(logging.WARNING)
logging.getLogger("chess.uci").setLevel(logging.WARNING)
logging.getLogger("chess.engine").setLevel(logging.WARNING)
logging.getLogger("chess._engine").setLevel(logging.WARNING)

engine = chess.uci.popen_engine(stockfish_command())
engine.setoption({'Threads': settings.threads, 'Hash': settings.memory})
engine.uci()
info_handler = chess.uci.InfoHandler()
engine.info_handlers.append(info_handler)

def updateGame(gameID: str) -> bool:
   client = pymongo.MongoClient('url')
   try:
      database = client["woodpecker-db"]
      collection = database["games"]
      setAsAnalyzed = { "$set" : { "analyzed": True }}
      collection.update_one({"game_id": gameID}, setAsAnalyzed)
      return True
   except Exception as err:
      print(err)
      return False

def insertPuzzle(puzzle) -> bool:
   client = pymongo.MongoClient('url')
   try:
      database = client["woodpecker-db"]
      collection = database["puzzles"]
      collection.insert_one(puzzle)
      return True
   except Exception as err:
      print(err)
      return False

client = pymongo.MongoClient('url')
try:
    database = client["woodpecker-db"]
    collection = database["games"]
    print(settings.user)
    for currentGame in collection.find({"user": settings.user}).limit(settings.max):
        if currentGame["analyzed"] == True:
            print(currentGame["game_id"])
            print("Already analyzed")
        else:
            print(currentGame["game_id"])
            print("Not analyzed yet")
            pgn = io.StringIO(currentGame["pgn"])
            game = chess.pgn.read_game(pgn)
            if game is None:
                break
            node = game

            game_id = currentGame["game_id"]
            logging.debug(bcolors.WARNING + "Game ID: " + str(game_id) + bcolors.ENDC)
            logging.debug(bcolors.WARNING + "Game headers: " + str(game) + bcolors.ENDC)

            prev_score = chess.uci.Score(None, None)

            logging.debug(bcolors.OKGREEN + "Game Length: " + str(game.end().board().fullmove_number))
            logging.debug("Analysing Game..." + bcolors.ENDC)

            engine.ucinewgame()

            while not node.is_end():
                next_node = node.variation(0)
                engine.position(next_node.board())

                engine.go(depth=settings.depth)
                cur_score = info_handler.info["score"][1]
                logging.debug(bcolors.OKGREEN + node.board().san(next_node.move) + bcolors.ENDC)
                logging.debug(bcolors.OKBLUE + "   CP: " + str(cur_score.cp))
                logging.debug("   Mate: " + str(cur_score.mate) + bcolors.ENDC)
                if investigate(prev_score, cur_score, node.board()):
                    logging.debug(bcolors.WARNING + "   Investigate!" + bcolors.ENDC)
                    logging.debug(bcolors.WARNING + "Generating new puzzle..." + bcolors.ENDC)
                    currentPuzzle = puzzle(node.board(), next_node.move, str(game_id), engine, info_handler, game, settings.strict)
                    currentPuzzle.generate(settings.depth)
                    if currentPuzzle.is_complete():
                        puzzle_pgn = post_puzzle(currentPuzzle, settings.include_blunder)
                        puzzle_json = currentPuzzle.to_json(settings.user, puzzle_pgn)
                        insertPuzzle(puzzle_json)
                prev_score = cur_score
                node = next_node
            updateGame(game_id)  
except Exception as err:
    print(err)
