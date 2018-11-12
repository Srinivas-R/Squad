import re
import os
import sys
import random
import string
import logging
import argparse
import json
import torch
import msgpack
import pandas as pd
import numpy as np
from shutil import copyfile
from datetime import datetime
from collections import Counter, defaultdict
from src.model import DocReaderModel
from src.batcher import load_meta, BatchGen
from config import set_args
from my_utils.utils import set_environment
from my_utils.log_wrapper import create_logger
from my_utils.squad_eval import evaluate
from my_utils.data_utils import predict_squad, gen_name, load_squad_v2_label, compute_acc, compute_classifier_pr_rec
from my_utils.squad_eval_v2 import my_evaluation as evaluate_v2
import pdb

args = set_args()
# set model dir
model_dir = args.model_dir
os.makedirs(model_dir, exist_ok=True)
model_dir = os.path.abspath(model_dir)

# set environment
set_environment(args.seed, args.cuda)
# setup logger
logger =  create_logger(__name__, to_disk=True, log_file=args.log_file)

def load_squad(data_path):
	with open(data_path) as dataset_file:
		dataset_json = json.load(dataset_file)
		dataset = dataset_json['data']
		return dataset

def main():
	logger.info('Launching the SAN')
	opt = vars(args)
	logger.info('Loading data')
	version = 'v1'
	if args.v2_on:
		version = 'v2'
		dev_labels = load_squad_v2_label(args.dev_gold)

	embedding, opt = load_meta(opt, gen_name(args.data_dir, args.meta, version, suffix='pick'))
	train_data = BatchGen(gen_name(args.data_dir, args.train_data, version),
						  batch_size=args.batch_size,
						  gpu=args.cuda,
						  with_label=args.v2_on)
	dev_data = BatchGen(gen_name(args.data_dir, args.dev_data, version),
						  batch_size=args.batch_size,
						  gpu=args.cuda, is_train=False)

	# load golden standard
	dev_gold = load_squad(args.dev_gold)

	model = DocReaderModel(opt, embedding)
	# model meta str
	headline = '############# Model Arch of SAN #############'
	# print network
	logger.info('\n{}\n{}\n'.format(headline, model.network))
	model.setup_eval_embed(embedding)

	logger.info("Total number of params: {}".format(model.total_param))
	if args.cuda:
		model.cuda()

	best_em_score, best_f1_score = 0.0, 0.0

	for epoch in range(0, args.epoches):
		logger.warning('At epoch {}'.format(epoch))
		train_data.reset()
		start = datetime.now()
		# for i, batch in enumerate(train_data):
		# 	#pdb.set_trace()
		# 	model.update(batch)
		# 	if (model.updates) % args.log_per_updates == 0 or i == 0:
		# 		logger.info('#updates[{0:6}] train loss[{1:.5f}] remaining[{2}]'.format(
		# 			model.updates, model.train_loss.avg,
		# 			str((datetime.now() - start) / (i + 1) * (len(train_data) - i - 1)).split('.')[0]))
		# dev eval
		results, labels = predict_squad(model, dev_data, v2_on=args.v2_on)
		if args.v2_on:
			metric = evaluate_v2(dev_gold, results, labels, na_prob_thresh=args.classifier_threshold)
			em, f1 = metric['exact'], metric['f1']
			acc = compute_acc(labels, dev_labels)
			cls_pr, cls_rec, cls_f1 = compute_classifier_pr_rec(labels, dev_labels)
		else:
			metric = evaluate(dev_gold, results)
			em, f1 = metric['exact_match'], metric['f1']

		output_path = os.path.join(model_dir, 'dev_output_{}.json'.format(epoch))
		with open(output_path, 'w') as f:
			json.dump(results, f)

		# setting up scheduler
		if model.scheduler is not None:
			logger.info('scheduler_type {}'.format(opt['scheduler_type']))
			if opt['scheduler_type'] == 'rop':
				model.scheduler.step(f1, epoch=epoch)
			else:
				model.scheduler.step()
		# save
		model_file = os.path.join(model_dir, 'checkpoint_{}_epoch_{}.pt'.format(version, epoch))

		model.save(model_file, epoch)
		if em + f1 > best_em_score + best_f1_score:
			copyfile(os.path.join(model_dir, model_file), os.path.join(model_dir, 'best_{}_checkpoint.pt'.format(version)))
			best_em_score, best_f1_score = em, f1
			logger.info('Saved the new best model and prediction')

		logger.warning("Epoch {0} - dev EM: {1:.3f} F1: {2:.3f} (best EM: {3:.3f} F1: {4:.3f})".format(epoch, em, f1, best_em_score, best_f1_score))
		if args.v2_on:
			logger.warning("Epoch {0} - Precision: {1:.4f}, Recall: {2:.4f}, F1: {3:.4f}, Accuracy: {4:.4f}".format(epoch, cls_pr, cls_rec, cls_f1, acc))
		if metric is not None:
			logger.warning("Detailed Metric at Epoch {0}: {1}".format(epoch, metric))

if __name__ == '__main__':
	main()
