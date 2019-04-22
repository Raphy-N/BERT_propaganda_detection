import pickle
import logging
import os 
from opt import opt
import itertools

def make_logger() -> None:
    if not os.path.exists("./exp/{}/{}".format(opt.classType, opt.expID)):
            try:
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))
            except FileNotFoundError:
                os.mkdir("./exp/{}".format(opt.classType))
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))
    print("Init Logger")
    logging.basicConfig(
    filename= ("./exp/{}/{}/log.txt".format(opt.classType, opt.expID)),
    filemode='a',
    level=logging.INFO,
    format='%(asctime)s, %(message)s')

    logging.getLogger().addHandler(logging.StreamHandler())
make_logger()

from tokenize_text import *
import tools.task3_scorer_onefile
from utils import *
import numpy as np
import pandas as pd
import torch
from pytorch_pretrained_bert import (BasicTokenizer, BertConfig,
                                     BertForMultiTask, BertTokenizer)
from pytorch_pretrained_bert.optimization import BertAdam, warmup_linear
from sklearn.metrics import f1_score
from sklearn.metrics import precision_recall_fscore_support as f1
from torch.optim import Adam
from torch.utils.data import DataLoader, RandomSampler, SequentialSampler, WeightedRandomSampler, TensorDataset
from early_stopping import EarlyStopping                            
from tqdm import tqdm, trange

import matplotlib.pyplot as plt 


def get_task2_labels(task3_label: list, scorred_labels: list) -> list:
    labels = []
    met = False
    for x in task3_label:
        for j in x:
            if j in scorred_labels:
                #print (x, j)
                labels.append(1)
                met = True
                break
        if not met:
            labels.append(0)
        met = False
    return labels

def get_task2(predictions: list, scorred_labels: list) -> list:
    preddi = []
    found = False
    for x in predictions:
        for j in x:
            if j in scorred_labels:
                preddi.append(1)
                found = True
                break
        if not found:
            preddi.append(0)
        found = False
    return preddi


def draw_curves(trainlosses, validlosses, f1scores, f1scores_word, task2_scores) -> None:
    x = list(range(len(validlosses)))
    # plotting the line 1 points  
    plt.plot(x, trainlosses, label = "Train loss") 

    # plotting the line 2 points  
    plt.plot(x, validlosses, label = "Validation losses") 

    # line 3 points 
    # plotting the line 2 points  
    plt.plot(x, f1scores, label = "F1 scores char level") 
    
    plt.plot(x, f1scores_word, label = "F1 scores word level") 
    plt.plot(x, task2_scores, label = "F1 scores task2") 

    # naming the x axis 
    plt.xlabel('Epochs') 
    # naming the y axis 
    plt.ylabel('Metric') 
    # giving a title to my graph 
    plt.title('Training Curves') 
    #plt.yscale('log')
    # show a legend on the plot 
    plt.legend() 
    plt.savefig("exp/{}/{}/learning_curves.png".format(opt.classType, opt.expID))
    
def main():
    os.environ['CUDA_VISIBLE_DEVICES']='0,1,2,3,4'
    scorred_labels = list(range(1,(opt.nLabels-2)))

    prop_tech_e, prop_tech, hash_token, end_token, p2id = settings(opt.techniques, opt.binaryLabel, opt.bio)
    logging.info("Training for class %s" % (opt.binaryLabel))
    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    n_gpu = torch.cuda.device_count(); 
    logging.info("GPUs Detected: %s" % (n_gpu))

    tokenizer = BertTokenizer.from_pretrained(opt.model, do_lower_case=opt.lowerCase);
    print (hash_token, end_token)
    # Load Tokenized train and validation datasets
    tr_inputs, tr_tags, tr_masks, tr_label = make_set(p2id, opt.trainDataset, tokenizer, opt.binaryLabel, hash_token, end_token)
    val_inputs, val_tags, val_masks, cleaned, flat_list_i, flat_list, flat_list_s, val_label = make_val_set(p2id, opt.evalDataset,
                                                                                             tokenizer, opt.binaryLabel, hash_token, end_token)
    blabels = get_task2_labels(tr_label, scorred_labels)
    blabelsd = get_task2_labels(val_label, scorred_labels)
    printable = tr_tags
    # ids, texts, _ = read_data(opt.testDataset, isLabels = False)
    # flat_list_i, flat_list, flat_list_s = test2list(ids, texts)
    truth_task2 = get_task2(val_tags, scorred_labels)
    
    logging.info("Dataset loaded")
    logging.info("Labels detected in train dataset: %s" % (np.unique(tr_tags)))
    logging.info("Labels detected in val dataset: %s" % (np.unique(val_tags)))

    # Balanced Sampling
    total_tags = np.zeros((opt.nLabels,))
    for x in tr_tags:
         total_tags = total_tags+np.bincount(x)
    
    probs = 1./total_tags
    train_tokenweights = probs[tr_tags]
    weightage = np.sum(train_tokenweights, axis=1)
       # Alternate method for weighting
    ws = np.ones((opt.nLabels,))
    ws[0] = 0
    
    ws[hash_token] = 0
    ws[end_token] = 0
    ws = ws+0.2
    prob = [max(x) for x in ws[tr_tags]]
    weightage = [x + y for x, y in zip(prob, (len(prob)*[0.1]))]    
    
    # Convert to pyTorch tensors
    tr_inputs = torch.tensor(tr_inputs)
    val_inputs = torch.tensor(val_inputs)
    tr_tags = torch.tensor(tr_tags)
    val_tags = torch.tensor(val_tags)
    tr_masks = torch.tensor(tr_masks)
    val_masks = torch.tensor(val_masks)

    tr_tags_s = torch.tensor(blabels)
    val_tags_s = torch.tensor(blabelsd)
    # Create Dataloaders
    train_data = TensorDataset(tr_inputs, tr_masks, tr_tags, tr_tags_s)
    train_sampler = WeightedRandomSampler(weights=weightage, num_samples=len(tr_tags),replacement=True)
    #train_sampler = RandomSampler(train_data)
    train_dataloader = DataLoader(train_data, sampler=train_sampler, batch_size=opt.trainBatch)

    valid_data = TensorDataset(val_inputs, val_masks, val_tags, val_tags_s)
    valid_sampler = SequentialSampler(valid_data)
    valid_dataloader = DataLoader(valid_data, sampler=valid_sampler, batch_size=opt.trainBatch)

    # Model Initialize
    model = BertForMultiTask.from_pretrained("bert-base-cased", num_labels_t=len(np.unique(tr_tags)), num_labels_s = len(np.unique(blabels)));

    loss_scale = 0
    warmup_proportion = 0.1
    num_train_optimization_steps = int(len(train_data) / opt.trainBatch ) * opt.nEpochs
    
    # Prepare optimizer
    param_optimizer = list(model.named_parameters())

    # hack to remove pooler, which is not usedpython train.py --expID test --trainDataset dataset_train.csv --evalDataset dataset_dev.csv --model bert-base-cased --LR 3e-5 --trainBatch 12 --nEpochs 1
    # thus it produce None grad that break apex
    param_optimizer = [n for n in param_optimizer if 'pooler' not in n[0]]

    no_decay = ['bias', 'LayerNorm.bias', 'LayerNorm.weight']
    optimizer_grouped_parameters = [
        {'params': [p for n, p in param_optimizer if not any(nd in n for nd in no_decay)], 'weight_decay': 0.01},
        {'params': [p for n, p in param_optimizer if any(nd in n for nd in no_decay)], 'weight_decay': 0.0}
    ]
    # t_total matters
    optimizer = BertAdam(optimizer_grouped_parameters,
                         lr=opt.LR,
                         warmup=warmup_proportion,
                         t_total=num_train_optimization_steps) 
    
    model.to(device)
    
    if n_gpu > 1:
        model = torch.nn.DataParallel(model)
        logging.info("Training beginning on: %s" % n_gpu)

    if opt.loadModel:
        logging.info('Loading Model from {}'.format(opt.loadModel))
        model.load_state_dict(torch.load(opt.loadModel))
        if not os.path.exists("./exp/{}/{}".format(opt.classType, opt.expID)):
            try:
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))
            except FileNotFoundError:
                os.mkdir("./exp/{}".format(opt.classType))
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))
    else:
        logging.info('Create new model')
        if not os.path.exists("./exp/{}/{}".format(opt.classType, opt.expID)):
            try:
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))
            except FileNotFoundError:
                os.mkdir("./exp/{}".format(opt.classType))
                os.mkdir("./exp/{}/{}".format(opt.classType, opt.expID))

    # F1 score shouldn't consider no-propaganda
    # and other auxiliary labels
    scorred_labels = list(range(1,(opt.nLabels-2)))

    global_step = 0
    nb_tr_steps = 0
    tr_loss = 0
    max_grad_norm = 1.0
    best = 0
    early_stopping = EarlyStopping(patience=opt.patience, verbose=True)
    train_losses = []
    valid_losses = []
    f1_scores = []
    f1_scores_word = []
    task2_scores = []
    for i in trange(opt.nEpochs, desc="Epoch"):
        # TRAIN loop
        # Start only if train flag was passed
        if (opt.train):
            model.train()
            tr_loss = 0
            nb_tr_examples, nb_tr_steps = 0, 0
            with (trange(len(train_dataloader))) as pbar: 
                for step, batch in enumerate(train_dataloader):
                    if n_gpu == 1:
                        batch = tuple(t.to(device) for t in batch)
                    b_input_ids, b_input_mask, b_labels, b_labels_s = batch
                    # forward pass
                    loss = model(b_input_ids, token_type_ids=None,
                         attention_mask=b_input_mask, labels_t=b_labels, labels_s=b_labels_s)
                    if n_gpu > 1:
                        loss = loss.mean()

                    # backward pass
                    loss.backward()
                    pbar.set_postfix({"Train Loss":loss.item()})
                    tr_loss += loss.item()
                    nb_tr_examples += b_input_ids.size(0)
                    nb_tr_steps += 1

                    optimizer.step()
                    optimizer.zero_grad()
                    global_step += 1
                    pbar.update(1)
            logging.info(f'EPOCH {i} done: Train Loss {(tr_loss/nb_tr_steps)}')
            train_losses.append(tr_loss/nb_tr_steps)
       
        # Evaluation on validation set or test set
        model.eval()
        eval_loss, eval_accuracy = 0, 0
        nb_eval_steps, nb_eval_examples = 0, 0
        predictions, predictions_s, true_labels, true_labels_s = [], [], [], []
        for batch in tqdm(valid_dataloader, desc="Evaluating"):
            batch = tuple(t.to(device) for t in batch)
            b_input_ids, b_input_mask, b_labels, b_labels_s = batch
            
            with torch.no_grad():
                tmp_eval_loss = model(b_input_ids, token_type_ids=None,
                                      attention_mask=b_input_mask, labels_t=b_labels, labels_s=b_labels_s)
                logits, logitt = model(b_input_ids, token_type_ids=None,
                            attention_mask=b_input_mask)
            logits = logits.detach().cpu().numpy()
            label_ids = b_labels.to('cpu').numpy()
            predictions.extend([list(p) for p in np.argmax(logits, axis=2)])
            true_labels.append(label_ids)
            
            #tmp_eval_accuracy = flat_accuracy(logits, label_ids)
            #logitt = F.softmax(logitt)
            logitt = logitt.detach().cpu().numpy()
            #predictions.append(logits)
            label_ids_s = b_labels_s.to('cpu').numpy()
            predictions_s.append(np.argmax(logitt, axis=1))

            true_labels_s.append(label_ids_s)           
            eval_loss += tmp_eval_loss.mean().item()
            #eval_accuracy += tmp_eval_accuracy
            
            nb_eval_examples += b_input_ids.size(0)
            nb_eval_steps += 1
        pred_task2 = get_task2(predictions, scorred_labels)
        f1_macro = f1(pred_task2, truth_task2, average=None)
        logging.info("Precision, Recall, F1-Score, Support Task2: {}".format(f1_macro))
        task2_scores.append(f1_macro[2][1])
        pickle.dump(printable, open( "output_.p", "wb"))
        eval_loss = eval_loss/nb_eval_steps
        logging.info("Validation loss: %s" % (eval_loss))    
        logging.info("Precision, Recall, F1-Score, Support: {}".format(f1(list(itertools.chain(*predictions)), list(itertools.chain(*val_tags)), average=None)))
        f1_macro = f1_score(list(itertools.chain(*predictions)), list(itertools.chain(*val_tags)), labels=scorred_labels, average="macro")
        logging.info("F1 Macro Dev Set: %s" % f1_macro)
        logging.info("Learning Rate: %s" % (optimizer.get_lr()[0]))
        valid_losses.append(eval_loss)
        logging.info("F1-Score Sentence: {}".format(f1(np.hstack(predictions_s), np.hstack(true_labels_s))))

        f1_scores_word.append(f1_macro)
        
        df = get_char_level(flat_list_i, flat_list_s, predictions, cleaned, hash_token, end_token, prop_tech)
        postfix = opt.testDataset.rsplit('/', 2)[-2]
        if opt.loadModel:
            out_dir = opt.loadModel.rsplit('/', 1)[0] + "/pred." + postfix
        else:
            out_dir = ("exp/{}/{}/temp_pred.csv".format(opt.classType, opt.expID))
        df.to_csv(out_dir, sep='\t', index=False, header=False) 
        logging.info("Predictions written to: %s" % (out_dir))

        if opt.loadModel:
            out_file = opt.loadModel.rsplit('/', 1)[0] + "/score." + postfix
        else:
            out_file = ("exp/{}/{}/temp_score.csv".format(opt.classType, opt.expID))

        if opt.classType != "binary":
            char_predict = tools.task3_scorer_onefile.main(["-s", out_dir, "-r", opt.testDataset, "-t", opt.techniques, "-l", out_file])
        else:
            char_predict = tools.task3_scorer_onefile.main(["-s", out_dir, "-r", opt.testDataset, "-t", opt.techniques, "-f", "-l", out_file])
        f1_scores.append(char_predict) 
        logging.info("Char level prediction: %s" % (char_predict))
         
        # early_stopping needs the validation loss to check if it has decresed, 
        # and if it has, it will make a checkpoint of the current model
        if not opt.train:
            break
        early_stopping(char_predict*(-1), model)
        
        if early_stopping.early_stop:
            logging.info("Early stopping")
            break
        # Save checkpoints
        if i % opt.snapshot == 0:
            if not os.path.exists("./exp/{}/{}/{}".format(opt.classType, opt.expID, i)):
                try:
                    os.mkdir("./exp/{}/{}/{}".format(opt.classType, opt.expID, i))
                except FileNotFoundError:
                    os.mkdir("./exp/{}/{}/{}".format(opt.classType, opt.expID, i))
            torch.save(
                model.state_dict(), './exp/{}/{}/{}/model_{}.pth'.format(opt.classType, opt.expID, i, i))
            torch.save(
                opt, './exp/{}/{}/{}/option.pth'.format(opt.classType, opt.expID, i))
            torch.save(
                optimizer, './exp/{}/{}/{}/optimizer.pth'.format(opt.classType, opt.expID, i))

        
        # Save model based on best F1 score and if epoch is greater than 3
        '''if f1_macro > best and i > 3:
        # Save a trained model and the associated configuration
            torch.save(
                model.state_dict(), './exp/{}/{}/best_model.pth'.format(opt.classType, opt.expID))
            torch.save(
                opt, './exp/{}/{}/option.pth'.format(opt.classType, opt.expID))
            torch.save(
                optimizer, './exp/{}/{}/optimizer.pth'.format(opt.classType, opt.expID))
            #model_to_save = model.module if hasattr(model, 'module') else model  # Only save the model it-self
            #output_model_file = os.path.join("./exp/{}/{}".format(opt.classType, opt.expID), "best_model.pth")
            #torch.save(model_to_save.state_dict(), output_model_file)
            best = f1_macro
            logging.info("New best model")
        '''
    if opt.train:
        logging.info("Training Finished. Learning curves saved.")
        draw_curves(train_losses, valid_losses, f1_scores, f1_scores_word, task2_scores)
        #df = pd.DataFrame({'col':trainlosses})
        #df.to_csv("trainlosses.csv", sep='\t', index=False, header=False) 
        #df = pd.DataFrame({'col':validlosses})
        #df.to_csv("validlosses.csv", sep='\t', index=False, header=False) 
        #df = pd.DataFrame({'col':f1scores})
        #df.to_csv("f1scores.csv", sep='\t', index=False, header=False) 
if __name__ == '__main__':
    main()